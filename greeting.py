"""Manage the photo, greeting, compliment, and petting phases."""

import threading
import time

from mistyPy.Events import Events

from face_utils import get_property
from misty_camera import take_picture
from ollama_client import describe_person

PET_REQUEST_DELAY = 2.0


class Greeting:
    """Run the human-facing parts of one interaction with Misty."""

    HEAD_SENSORS = {"headfront", "headback", "headleft", "headright", "scruff"}

    def __init__(self, misty, take_photo=None):
        self.misty = misty
        # Returns JPEG bytes; defaults to Misty's own camera.
        self._take_photo = take_photo or (lambda: take_picture(misty))
        self.head_petted = threading.Event()
        self.petted = threading.Event()
        self.shutting_down = threading.Event()
        self.photo_capture_done = threading.Event()
        self.photo_captured = threading.Event()
        self.description_done = threading.Event()
        self.compliment_done = threading.Event()
        self.waiting_for_pet = threading.Event()
        self.done = threading.Event()
        self._worker_lock = threading.Lock()
        self._active_workers = 0
        self._compliment_started = False
        self._announce = True
        self._speech_event_index = 0
        self._photo = None
        self._detail = None
        self._interaction_id = 0

    def register(self) -> None:
        self.misty.register_event(
            Events.TouchSensor,
            "GreetingHeadPetting",
            keep_alive=True,
            callback_function=lambda event: self._on_touch_event(event),
        )

    def start(self, announce: bool = True) -> None:
        """Capture the photo before the approach begins.

        announce=False skips the "I see you" line, which is said for the first face of
        a run and again after a face was lost mid-interaction.
        """
        self._announce = announce
        self.head_petted.clear()
        self.petted.clear()
        self.shutting_down.clear()
        self.photo_capture_done.clear()
        self.photo_captured.clear()
        self.description_done.clear()
        self.compliment_done.clear()
        self.waiting_for_pet.clear()
        self.done.clear()
        self._compliment_started = False
        self._photo = None
        self._detail = None
        self._interaction_id += 1
        self._launch(self._capture_photo)

    def start_arrival_interaction(self) -> None:
        """Speak the greeting and compliment, then begin the petting phase."""
        with self._worker_lock:
            if self._compliment_started or self.shutting_down.is_set():
                return
            self._compliment_started = True
        self._launch(self._greet_compliment_and_pet)

    def stop(self) -> None:
        self.shutting_down.set()
        with self._worker_lock:
            should_stop_speech = self._active_workers > 0
            if not should_stop_speech:
                self.done.set()
        if should_stop_speech:
            try:
                self.misty.stop_speaking().raise_for_status()
            except Exception as error:
                print(f"Could not stop speech: {error}")

    def _launch(self, target) -> None:
        with self._worker_lock:
            self._active_workers += 1

        def run_worker():
            try:
                target()
            except Exception as error:
                print(f"Could not complete the greeting interaction: {error}")
            finally:
                with self._worker_lock:
                    self._active_workers -= 1
                    if self.shutting_down.is_set() and self._active_workers == 0:
                        self.done.set()

        threading.Thread(target=run_worker, daemon=True).start()

    def _capture_photo(self) -> None:
        try:
            if self._announce:
                self._speak_and_wait("I see you")
                if self.shutting_down.is_set():
                    return
            print("Taking a picture before approaching...")
            self._photo = self._take_photo()
            if self.shutting_down.is_set():
                return
            interaction_id = self._interaction_id
            threading.Thread(
                target=self._describe_photo,
                args=(self._photo, interaction_id),
                daemon=True,
            ).start()
            self.photo_captured.set()
        except Exception as error:
            print(f"Could not take the picture: {error}")
            self.done.set()
        finally:
            self.photo_capture_done.set()

    def _describe_photo(self, photo: bytes, interaction_id: int) -> None:
        try:
            detail = describe_person(photo)
        except Exception as error:
            print(f"Could not describe the photo: {error}")
            detail = None
        if interaction_id == self._interaction_id and not self.shutting_down.is_set():
            self._detail = detail
            self.description_done.set()

    def _greet_compliment_and_pet(self) -> None:
        try:
            if self._photo is None or self.shutting_down.is_set():
                return
            self._wave()
            if self.shutting_down.is_set():
                return
            self._speak_and_wait("Hi, I'm Misty!")
            if self.shutting_down.is_set():
                return
            if not self.description_done.is_set():
                self._speak_and_wait("Trying to think of a compliment")
            while not self.description_done.wait(0.1):
                if self.shutting_down.is_set():
                    return
            if self.shutting_down.is_set():
                return
            if self._detail and self._detail != "clothes":
                phrase = f"Nice to meet you. I like your {self._detail}."
                print(f"Misty says: {phrase}")
                self._speak_and_wait(phrase)
            if self.shutting_down.is_set():
                return
            self.compliment_done.set()
            if self.shutting_down.wait(PET_REQUEST_DELAY):
                return
            self._ask_to_be_petted()
        except Exception as error:
            print(f"Could not give the compliment or ask to be petted: {error}")
            self.done.set()
        finally:
            if self._compliment_started and not self.shutting_down.is_set():
                self.done.set()

    def _speak_and_wait(self, text: str) -> None:
        self._speech_event_index += 1
        event_name = f"GreetingSpeechComplete{self._speech_event_index}"
        completed = threading.Event()
        subscription = self.misty.register_event(
            Events.TextToSpeechComplete,
            event_name,
            keep_alive=False,
            callback_function=lambda event: completed.set(),
        )

        deadline = time.monotonic() + 5
        while getattr(subscription, "initial_flag", False) and time.monotonic() < deadline:
            if self.shutting_down.is_set():
                self.misty.unregister_event(event_name)
                return
            time.sleep(0.05)

        if getattr(subscription, "initial_flag", False):
            self.misty.unregister_event(event_name)
            raise TimeoutError("Timed out waiting for Misty's speech event subscription")

        try:
            self.misty.speak(
                text,
                flush=True,
                utteranceId=event_name,
                language="en-US",
            ).raise_for_status()
            deadline = time.monotonic() + 30
            while not completed.wait(0.1):
                if self.shutting_down.is_set():
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for Misty to finish saying: {text}")
        finally:
            if not completed.is_set():
                self.misty.unregister_event(event_name)

    def _on_touch_event(self, event) -> None:
        message = get_property(event, "message", {})
        position = str(get_property(message, "sensorPosition", "")).casefold()
        if (
            position in self.HEAD_SENSORS
            and get_property(message, "isContacted", False)
            and self.waiting_for_pet.is_set()
        ):
            self.head_petted.set()
            self.petted.set()

    def _wave(self) -> None:
        for position in (55, 35, 55, 35, 55, 0):
            self.misty.move_arm("right", position, 35, units="degrees").raise_for_status()

    def _ask_to_be_petted(self) -> None:
        self.waiting_for_pet.set()
        while not self.head_petted.is_set() and not self.shutting_down.is_set():
            started = time.monotonic()
            self._speak_and_wait("Would you like to pet my head?")
            wait_time = max(0, 3.0 - (time.monotonic() - started))
            self.shutting_down.wait(wait_time)
        if self.head_petted.is_set() and not self.shutting_down.is_set():
            self._speak_and_wait("Thank you!")
