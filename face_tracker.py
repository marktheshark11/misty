"""PC-side face detection, identity locking and tracking in world-angle space.

Why this fixes "snapping to other people": the tracker locks onto one person and keeps
an appearance embedding (SFace) of them. A different face is only accepted if it is
where the target is predicted to be *and* doesn't look like someone else, or if it
matches the target's embedding. When the target is briefly lost, the tracker coasts
on its motion estimate and then waits for that same person before picking anyone new.

Faces are tracked as world angles (head pose at capture time + angle within the
image), so head motion and stream latency don't disturb the association.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

SEARCHING, TRACKING, COASTING, REACQUIRING = "searching", "tracking", "coasting", "reacquiring"


@dataclass
class TrackerConfig:
    hfov_deg: float = 70.0          # Horizontal field of view of the streamed image.
    score_threshold: float = 0.7    # YuNet detection confidence.
    min_face_px: int = 24           # Ignore tiny faces (far away / false positives).
    min_face_px_for_id: int = 40    # Below this, embeddings are unreliable.
    sim_match: float = 0.40         # Cosine similarity: same person (SFace suggests 0.363).
    sim_uncertain: float = 0.30     # Between this and sim_match: "maybe", tolerated briefly.
    max_uncertain_frames: int = 15  # ~0.5 s of "maybe" before treating it as someone else.
    gate_deg: float = 7.0           # Base association radius around the prediction.
    max_speed_deg: float = 60.0     # Max plausible face angular speed for gating.
    coast_time: float = 0.8         # Keep aiming at the prediction this long after a miss.
    reacquire_time: float = 3.0     # Then wait this long for the *same* person.
    confirm_frames: int = 2         # Frames a new face must persist before locking on.
    id_check_frames: int = 4        # Re-verify a lone target's identity this often.
    alpha: float = 0.55             # Alpha-beta filter gains.
    beta: float = 0.12


@dataclass
class Face:
    row: np.ndarray                 # Raw YuNet output (box + landmarks + score).
    yaw: float                      # World angles (degrees) of the face centre.
    pitch: float
    width_px: float
    feature: np.ndarray | None = None
    similarity: float | None = None

    @property
    def box(self):
        x, y, w, h = self.row[:4]
        return int(x), int(y), int(w), int(h)


@dataclass
class TrackResult:
    state: str
    yaw: float | None = None
    pitch: float | None = None
    vel_yaw: float = 0.0
    vel_pitch: float = 0.0
    target: Face | None = None
    faces: list = field(default_factory=list)
    similarity: float | None = None


class FaceTracker:
    def __init__(self, detector_model: str, recognizer_model: str, config: TrackerConfig):
        self.cfg = config
        self.detector = cv2.FaceDetectorYN.create(
            detector_model, "", (640, 480), config.score_threshold, 0.3, 50)
        self.recognizer = cv2.FaceRecognizerSF.create(recognizer_model, "")
        self._input_size = (640, 480)
        self._reset()

    # ----- state -------------------------------------------------------------------------
    def _reset(self) -> None:
        self.state = SEARCHING
        self.yaw = self.pitch = 0.0
        self.vy = self.vp = 0.0
        self.t = 0.0
        self.last_seen = 0.0
        self.anchor = None      # Embedding captured at lock-on (prevents drift).
        self.template = None    # Running-average embedding (adapts to pose/light).
        self.frames_since_id = 0
        self._uncertain_frames = 0
        self._pose_uncertainty = 0.0
        self._candidate = None
        self._candidate_count = 0

    # ----- geometry ----------------------------------------------------------------------
    def _to_world(self, row, shape, head_yaw, head_pitch):
        h, w = shape[:2]
        fx = (w / 2.0) / math.tan(math.radians(self.cfg.hfov_deg) / 2.0)
        cx = row[0] + row[2] / 2.0
        cy = row[1] + row[3] / 2.0
        ax = math.degrees(math.atan((cx - w / 2.0) / fx))
        ay = math.degrees(math.atan((cy - h / 2.0) / fx))
        # Image right = robot yaw negative; image down = robot pitch positive.
        return head_yaw - ax, head_pitch + ay

    def _angular_width(self, width_px, image_w):
        return width_px / image_w * self.cfg.hfov_deg

    # ----- appearance --------------------------------------------------------------------
    def _feature(self, frame, face: Face):
        if face.feature is None and face.width_px >= self.cfg.min_face_px_for_id:
            aligned = self.recognizer.alignCrop(frame, face.row)
            feat = self.recognizer.feature(aligned).flatten().astype(np.float32)
            face.feature = feat / (np.linalg.norm(feat) + 1e-9)
        return face.feature

    def _similarity(self, frame, face: Face):
        if self.anchor is None:
            return None
        feat = self._feature(frame, face)
        if feat is None:
            return None
        face.similarity = float(max(feat @ self.anchor, feat @ self.template))
        return face.similarity

    def _update_template(self, frame, face: Face) -> None:
        feat = self._feature(frame, face)
        if feat is None:
            return
        if self.anchor is None:
            self.anchor = feat.copy()
            self.template = feat.copy()
        else:
            blended = 0.85 * self.template + 0.15 * feat
            self.template = blended / (np.linalg.norm(blended) + 1e-9)
        self.frames_since_id = 0

    # ----- main entry point --------------------------------------------------------------
    def detect(self, frame, head_yaw, head_pitch):
        h, w = frame.shape[:2]
        if self._input_size != (w, h):
            self.detector.setInputSize((w, h))
            self._input_size = (w, h)
        _, rows = self.detector.detect(frame)
        faces = []
        if rows is not None:
            for row in rows:
                if row[2] < self.cfg.min_face_px:
                    continue
                yaw, pitch = self._to_world(row, frame.shape, head_yaw, head_pitch)
                faces.append(Face(row=row, yaw=yaw, pitch=pitch, width_px=float(row[2])))
        return faces

    def update(self, frame, head_yaw, head_pitch, t=None, pose_uncertainty=0.0) -> TrackResult:
        """pose_uncertainty: degrees the head pose may be off for this frame (e.g. the head
        was mid-move and its position is only estimated). Such frames still keep the lock,
        with a wider gate, but don't move the face estimate, so a wrong pose can't drag
        the head after a phantom position."""
        t = time.monotonic() if t is None else t
        self._pose_uncertainty = pose_uncertainty
        faces = self.detect(frame, head_yaw, head_pitch)
        if self.state == SEARCHING:
            return self._search(frame, faces, t)
        return self._follow(frame, faces, t)

    def _search(self, frame, faces, t) -> TrackResult:
        if not faces:
            self._candidate, self._candidate_count = None, 0
            return TrackResult(SEARCHING, faces=faces)
        # Prefer the largest (closest) face, tie-broken towards the image centre.
        best = max(faces, key=lambda f: f.width_px - 0.3 * abs(f.yaw - self.yaw))
        if self._candidate is not None and math.hypot(
                best.yaw - self._candidate[0], best.pitch - self._candidate[1]) < self.cfg.gate_deg:
            self._candidate_count += 1
        else:
            self._candidate_count = 1
        self._candidate = (best.yaw, best.pitch)
        if self._candidate_count < self.cfg.confirm_frames:
            return TrackResult(SEARCHING, faces=faces)

        self.state = TRACKING
        self.yaw, self.pitch, self.vy, self.vp = best.yaw, best.pitch, 0.0, 0.0
        self.t = self.last_seen = t
        self.anchor = self.template = None
        self._update_template(frame, best)
        print("Locked onto a face.")
        return self._result(best, faces)

    def _follow(self, frame, faces, t) -> TrackResult:
        cfg = self.cfg
        dt = max(1e-3, t - self.t)
        pred_yaw, pred_pitch = self.yaw + self.vy * dt, self.pitch + self.vp * dt
        since_seen = t - self.last_seen
        image_w = frame.shape[1]

        chosen = None
        best_sim = None
        in_gate = []
        if faces:
            gate = (cfg.gate_deg + min(since_seen, 0.5) * cfg.max_speed_deg
                    + self._pose_uncertainty)
            for face in faces:
                dist = math.hypot(face.yaw - pred_yaw, face.pitch - pred_pitch)
                if dist <= gate + 0.5 * self._angular_width(face.width_px, image_w):
                    in_gate.append((dist, face))
            in_gate.sort(key=lambda item: item[0])

            # Identity is checked whenever there's any doubt, and periodically otherwise.
            # Steady single-face tracking skips it most frames to save ~10 ms per face.
            need_id = (self.state != TRACKING or len(faces) > 1
                       or self.frames_since_id >= cfg.id_check_frames)

            # Once REACQUIRING, position means nothing (someone else may have stepped into
            # the spot); only the target's identity can bring the lock back.
            if self.state in (TRACKING, COASTING) and in_gate:
                nearest = in_gate[0][1]
                if not need_id:
                    chosen = nearest
                else:
                    known = [(s, d, f) for d, f in in_gate
                             if (s := self._similarity(frame, f)) is not None]
                    if known:
                        s, _, f = max(known, key=lambda item: item[0] - 0.01 * item[1])
                        if s >= cfg.sim_match:
                            chosen, best_sim = f, s
                        elif (s >= cfg.sim_uncertain and f is nearest
                              and self._uncertain_frames < cfg.max_uncertain_frames):
                            # Same spot, ambiguous look (turned head, motion blur): keep
                            # following briefly, but never learn from it.
                            chosen, best_sim = f, s
                    elif len(in_gate) == 1:
                        chosen = nearest  # Too small to identify; trust motion.

            uncertain = chosen is None or best_sim is None or best_sim < cfg.sim_match
            if self.anchor is not None and need_id and uncertain:
                # A confident identity match anywhere in view beats a doubtful local pick.
                for face in faces:
                    s = self._similarity(frame, face)
                    if s is not None and s >= cfg.sim_match and (best_sim is None or s > best_sim):
                        chosen, best_sim = face, s

        if chosen is not None:
            if best_sim is not None and best_sim < cfg.sim_match:
                self._uncertain_frames += 1
            elif best_sim is not None:
                self._uncertain_frames = 0
            jumped = self.state == REACQUIRING or all(f is not chosen for _, f in in_gate)
            self._correct(chosen, pred_yaw, pred_pitch, dt, t, reset_velocity=jumped)
            if jumped:
                print("Re-acquired the same person.")
            self.state = TRACKING
            self.frames_since_id += 1
            if best_sim is not None and best_sim >= cfg.sim_match:
                self._update_template(frame, chosen)  # Only ever learn from confirmed matches.
            elif self.anchor is None:
                self._update_template(frame, chosen)  # Locked on while too small to identify.
            return self._result(chosen, faces, best_sim)

        # No match this frame.
        self.t = t
        if since_seen <= cfg.coast_time:
            self.state = COASTING
            self.yaw, self.pitch = pred_yaw, pred_pitch
            self.vy *= 0.85
            self.vp *= 0.85
            return TrackResult(COASTING, self.yaw, self.pitch, self.vy, self.vp, faces=faces)
        if since_seen <= cfg.coast_time + cfg.reacquire_time:
            if self.state != REACQUIRING:
                print("Face lost; waiting for the same person...")
            self.state = REACQUIRING
            self.vy = self.vp = 0.0
            return TrackResult(REACQUIRING, self.yaw, self.pitch, faces=faces)
        print("Target gone; looking for anyone.")
        last_yaw = self.yaw
        self._reset()
        self.yaw = last_yaw
        return TrackResult(SEARCHING, faces=faces)

    def _correct(self, face: Face, pred_yaw, pred_pitch, dt, t, reset_velocity=False) -> None:
        if self._pose_uncertainty > 1.5 and not reset_velocity:
            # Seen, but the pose is unreliable: stay locked, and let the motion estimate
            # fade rather than extrapolating it blindly until the pose is trustworthy.
            self.yaw, self.pitch = pred_yaw, pred_pitch
            self.vy *= 0.8
            self.vp *= 0.8
        elif reset_velocity:
            self.yaw, self.pitch, self.vy, self.vp = face.yaw, face.pitch, 0.0, 0.0
        else:
            a, b = self.cfg.alpha, self.cfg.beta
            ry, rp = face.yaw - pred_yaw, face.pitch - pred_pitch
            self.yaw, self.pitch = pred_yaw + a * ry, pred_pitch + a * rp
            limit = self.cfg.max_speed_deg
            self.vy = max(-limit, min(limit, self.vy + b * ry / dt))
            self.vp = max(-limit, min(limit, self.vp + b * rp / dt))
        self.t = self.last_seen = t

    def _result(self, face, faces, similarity=None) -> TrackResult:
        return TrackResult(self.state, self.yaw, self.pitch, self.vy, self.vp,
                           target=face, faces=faces, similarity=similarity)
