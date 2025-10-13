#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Community Integration for bey (Beyond Presence).

Generate real-time video avatars for your Pipecat AI agents with Beyond Presence.
"""

import asyncio
from functools import partial
from typing import Any, Awaitable, Callable, Mapping, Optional, Tuple

import aiohttp
from daily.daily import AudioData
from loguru import logger
from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import (
    DailyCallbacks,
    DailyParams,
    DailyTransportClient,
)
from pipecat.transports.daily.utils import (
    DailyMeetingTokenParams,
    DailyMeetingTokenProperties,
    DailyRESTHelper,
)
from pydantic import BaseModel

BASE_API_URL = "https://api.bey.dev/v1"
FRAME_RATE = 25

_BEY_AVATAR_BOT_NAME = "bey-avatar"


class BeyApi:
    """Helper class for interacting with the Beyond Presence API.

    Provides methods for creating and managing avatar sessions with Beyond Presence,
    including session lifecycle management.
    """

    def __init__(self, api_key: str, session: aiohttp.ClientSession):
        """Initialize the BeyApi client.

        Args:
            api_key: Beyond Presence API key for authentication.
            session: An aiohttp session for making HTTP requests.
        """
        self._api_key = api_key
        self._session = session
        self._headers = {"x-api-key": self._api_key}

    async def create_session(self, avatar_id: str, room_url: str, token: str) -> dict[str, Any]:
        """Create a new Beyond Presence session.

        Args:
            avatar_id: ID of the avatar to use in the session.
            room_url: Daily room URL where the session will take place.
            token: Daily meeting token for the avatar bot.

        Returns:
            Dictionary containing session information.
        """
        logger.debug(f"Creating Bey session: avatar_id={avatar_id}, room_url={room_url}")
        url = f"{BASE_API_URL}/session"
        payload = {
            "avatar_id": avatar_id,
            "transport_type": "pipecat",
            "pipecat_url": room_url,
            "pipecat_token": token,
        }
        async with self._session.post(url, headers=self._headers, json=payload) as r:
            if not r.ok:
                text = await r.text()
                raise Exception(f"Bey API returned error {r.status}: {text}")
            response = await r.json()
            logger.debug(f"Created Bey session: {response}")
            return response


class BeyCallbacks(BaseModel):
    """Callback handlers for Bey transport events.

    Parameters:
        on_participant_joined: Called when a participant joins the conversation.
        on_participant_left: Called when a participant leaves the conversation.
    """

    on_participant_joined: Callable[[Mapping[str, Any]], Awaitable[None]]
    on_participant_left: Callable[[Mapping[str, Any], str], Awaitable[None]]


class BeyParams(DailyParams):
    """Configuration parameters for the Bey transport.

    Parameters:
        audio_in_enabled: Whether to enable audio input from participants.
        audio_out_enabled: Whether to enable audio output to participants.
        microphone_out_enabled: Whether to enable microphone output track.
    """

    audio_in_enabled: bool = True
    audio_out_enabled: bool = True
    microphone_out_enabled: bool = False


class BeyTransportClient:
    """Transport client that integrates Pipecat with the Beyond Presence platform.

    A transport client that integrates a Pipecat Bot with the Beyond Presence platform by
    managing avatar sessions using the Beyond Presence API.

    This client uses `BeyApi` to interact with the Beyond Presence backend services. When a
    session is started via `BeyApi`, Beyond Presence joins the provided Daily room with a Bey
    avatar bot that generates video for the Pipecat bot.
    """

    def __init__(
        self,
        *,
        bot_name: str,
        params: BeyParams = BeyParams(),
        callbacks: BeyCallbacks,
        api_key: str,
        avatar_id: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the Bey transport client.

        Args:
            bot_name: The name of the Pipecat bot instance.
            params: Optional parameters for Bey operation.
            callbacks: Callback handlers for Bey-related events.
            api_key: API key for authenticating with Beyond Presence API.
            avatar_id: ID of the avatar to use in the Bey session.
            session: The aiohttp session for making async HTTP requests.
        """
        self._bot_name = bot_name
        self._api = BeyApi(api_key, session)
        self._avatar_id = avatar_id
        self._client: Optional[DailyTransportClient] = None
        self._callbacks = callbacks
        self._params = params

        self._is_initialized = False

    async def _initialize(self, room_url: str, token: str) -> None:
        """Initialize the session with Beyond Presence.

        Args:
            room_url: Daily room URL for the session.
            token: Daily meeting token for authentication.
        """
        if self._is_initialized:
            return
        self._is_initialized = True

        await self._api.create_session(self._avatar_id, room_url, token)

    async def setup(self, setup: FrameProcessorSetup, room_url: str, token: str):
        """Setup the client and initialize the session.

        Args:
            setup: The frame processor setup configuration.
            room_url: Daily room URL for the session.
            token: Daily meeting token for authentication.
        """
        try:
            await self._initialize(room_url, token)
            daily_callbacks = DailyCallbacks(
                on_active_speaker_changed=partial(
                    self._on_handle_callback, "on_active_speaker_changed"
                ),
                on_joined=self._on_joined,
                on_left=self._on_left,
                on_before_leave=partial(self._on_handle_callback, "on_before_leave"),
                on_error=partial(self._on_handle_callback, "on_error"),
                on_app_message=partial(self._on_handle_callback, "on_app_message"),
                on_call_state_updated=partial(self._on_handle_callback, "on_call_state_updated"),
                on_client_connected=partial(self._on_handle_callback, "on_client_connected"),
                on_client_disconnected=partial(self._on_handle_callback, "on_client_disconnected"),
                on_dialin_connected=partial(self._on_handle_callback, "on_dialin_connected"),
                on_dialin_ready=partial(self._on_handle_callback, "on_dialin_ready"),
                on_dialin_stopped=partial(self._on_handle_callback, "on_dialin_stopped"),
                on_dialin_error=partial(self._on_handle_callback, "on_dialin_error"),
                on_dialin_warning=partial(self._on_handle_callback, "on_dialin_warning"),
                on_dialout_answered=partial(self._on_handle_callback, "on_dialout_answered"),
                on_dialout_connected=partial(self._on_handle_callback, "on_dialout_connected"),
                on_dialout_stopped=partial(self._on_handle_callback, "on_dialout_stopped"),
                on_dialout_error=partial(self._on_handle_callback, "on_dialout_error"),
                on_dialout_warning=partial(self._on_handle_callback, "on_dialout_warning"),
                on_participant_joined=self._callbacks.on_participant_joined,
                on_participant_left=self._callbacks.on_participant_left,
                on_participant_updated=partial(self._on_handle_callback, "on_participant_updated"),
                on_transcription_message=partial(
                    self._on_handle_callback, "on_transcription_message"
                ),
                on_recording_started=partial(self._on_handle_callback, "on_recording_started"),
                on_recording_stopped=partial(self._on_handle_callback, "on_recording_stopped"),
                on_recording_error=partial(self._on_handle_callback, "on_recording_error"),
                on_transcription_stopped=partial(
                    self._on_handle_callback, "on_transcription_stopped"
                ),
                on_transcription_error=partial(self._on_handle_callback, "on_transcription_error"),
            )
            self._client = DailyTransportClient(
                room_url, token, "Pipecat", self._params, daily_callbacks, self._bot_name
            )
            await self._client.setup(setup)
        except Exception as e:
            logger.error(f"Failed to setup BeyTransportClient: {e}")
            raise

    async def cleanup(self):
        """Cleanup client resources."""
        try:
            if self._client:
                await self._client.cleanup()
        except Exception as e:
            logger.exception(f"Exception during cleanup: {e}")

    async def _on_joined(self, data):
        """Handle joined event."""
        logger.debug("BeyTransportClient joined!")

    async def _on_left(self):
        """Handle left event."""
        logger.debug("BeyTransportClient left!")

    async def _on_handle_callback(self, event_name, *args, **kwargs):
        """Handle generic callback events."""
        logger.trace(f"[Callback] {event_name} called with args={args}, kwargs={kwargs}")

    async def start(self, frame: StartFrame):
        """Start the client and join the room.

        Args:
            frame: The start frame containing initialization parameters.
        """
        logger.debug("BeyTransportClient start invoked!")
        await self._client.start(frame)
        await self._client.join()

    async def stop(self):
        """Stop the client and leave the room."""
        if self._client:
            await self._client.leave()

    async def capture_participant_video(
        self,
        participant_id: str,
        callback: Callable,
        framerate: int = 30,
        video_source: str = "camera",
        color_format: str = "RGB",
    ):
        """Capture video from a participant.

        Args:
            participant_id: ID of the participant to capture video from.
            callback: Callback function to handle video frames.
            framerate: Desired framerate for video capture.
            video_source: Video source to capture from.
            color_format: Color format for video frames.
        """
        await self._client.capture_participant_video(
            participant_id, callback, framerate, video_source, color_format
        )

    async def capture_participant_audio(
        self,
        participant_id: str,
        callback: Callable,
        audio_source: str = "microphone",
        sample_rate: int = 16000,
        callback_interval_ms: int = 20,
    ):
        """Capture audio from a participant.

        Args:
            participant_id: ID of the participant to capture audio from.
            callback: Callback function to handle audio data.
            audio_source: Audio source to capture from.
            sample_rate: Desired sample rate for audio capture.
            callback_interval_ms: Interval between audio callbacks in milliseconds.
        """
        await self._client.capture_participant_audio(
            participant_id, callback, audio_source, sample_rate, callback_interval_ms
        )

    async def send_message(
        self, frame: OutputTransportMessageFrame | OutputTransportMessageUrgentFrame
    ):
        """Send a message to participants.

        Args:
            frame: The message frame to send.
        """
        await self._client.send_message(frame)

    @property
    def out_sample_rate(self) -> int:
        """Get the output sample rate.

        Returns:
            The output sample rate in Hz.
        """
        return self._client.out_sample_rate

    @property
    def in_sample_rate(self) -> int:
        """Get the input sample rate.

        Returns:
            The input sample rate in Hz.
        """
        return self._client.in_sample_rate

    async def send_interrupt_message(self) -> None:
        """Send an interrupt message to the conversation."""
        transport_frame = OutputTransportMessageFrame(message="interrupt")
        await self.send_message(transport_frame)

    async def update_subscriptions(self, participant_settings=None, profile_settings=None):
        """Update subscription settings for participants.

        Args:
            participant_settings: Per-participant subscription settings.
            profile_settings: Global subscription profile settings.
        """
        if not self._client:
            return

        await self._client.update_subscriptions(
            participant_settings=participant_settings, profile_settings=profile_settings
        )

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        """Write an audio frame to the transport.

        Args:
            frame: The audio frame to write.

        Returns:
            True if the audio frame was written successfully, False otherwise.
        """
        if not self._client:
            return False
        return await self._client.write_audio_frame(frame)

    async def register_audio_destination(self, destination: str):
        """Register an audio destination for output.

        Args:
            destination: The destination identifier to register.
        """
        if not self._client:
            return

        await self._client.register_audio_destination(destination)


class DailyCredentialsProvider:
    """Provides Daily room URL and meeting token.

    This class is responsible for providing the Daily room URL and meeting token
    needed by the Bey avatar bot to join the conversation.
    """

    def __init__(self, daily_rest_helper: DailyRESTHelper, room_url: str) -> None:
        """Initialize the DailyCredentialsProvider.

        Args:
            daily_rest_helper: An instance of DailyRESTHelper for fetching tokens.
            room_url: The Daily room URL where the session will take place.
        """
        self._daily_rest_helper = daily_rest_helper
        self._room_url = room_url

        self._future: Optional[asyncio.Future] = None

    def get_url(self) -> str:
        """Get the Daily room URL.

        Returns:
            The Daily room URL as a string.
        """
        return self._room_url

    async def get_token(self) -> str:
        """Get the cached Daily meeting token, fetching it if not already cached.

        Returns:
            The Daily meeting token as a string.
        """
        if self._future is None:
            self._future = asyncio.create_task(self._retrieve_token())
        return await self._future

    async def _retrieve_token(self) -> str:
        return await self._daily_rest_helper.get_token(
            room_url=self._room_url,
            params=DailyMeetingTokenParams(
                properties=DailyMeetingTokenProperties(user_name=_BEY_AVATAR_BOT_NAME),
            ),
            expiry_time=3600,  # 1 hour
        )


class BeyInputTransport(BaseInputTransport):
    """Input transport for receiving audio and events from Bey conversations.

    Handles incoming audio streams from participants and manages audio capture
    from the Daily room connected to the Bey conversation.
    """

    def __init__(
        self,
        client: BeyTransportClient,
        params: TransportParams,
        daily_credentials_provider: DailyCredentialsProvider,
        **kwargs,
    ):
        """Initialize the Bey input transport.

        Args:
            client: The Bey transport client instance.
            params: Transport configuration parameters.
            daily_credentials_provider: Provider for Daily room URL and token.
            **kwargs: Additional arguments passed to parent class.
        """
        super().__init__(params, **kwargs)
        self._client = client
        self._params = params
        self._daily_credentials_provider = daily_credentials_provider
        # Whether we have seen a StartFrame already.
        self._initialized = False

    async def setup(self, setup: FrameProcessorSetup):
        """Setup the input transport.

        Args:
            setup: The frame processor setup configuration.
        """
        await super().setup(setup)
        room_url = self._daily_credentials_provider.get_url()
        token = await self._daily_credentials_provider.get_token()
        await self._client.setup(setup, room_url, token)

    async def cleanup(self):
        """Cleanup input transport resources."""
        await super().cleanup()
        await self._client.cleanup()

    async def start(self, frame: StartFrame):
        """Start the input transport.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)

        if self._initialized:
            return

        self._initialized = True

        await self._client.start(frame)
        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame):
        """Stop the input transport.

        Args:
            frame: The end frame signaling transport shutdown.
        """
        await super().stop(frame)
        await self._client.stop()

    async def cancel(self, frame: CancelFrame):
        """Cancel the input transport.

        Args:
            frame: The cancel frame signaling immediate cancellation.
        """
        await super().cancel(frame)
        await self._client.stop()

    async def start_capturing_audio(self, participant):
        """Start capturing audio from a participant.

        Args:
            participant: The participant to capture audio from.
        """
        if self._params.audio_in_enabled:
            logger.info(
                f"BeyTransportClient start capturing audio for participant {participant['id']}"
            )
            await self._client.capture_participant_audio(
                participant_id=participant["id"],
                callback=self._on_participant_audio_data,
                sample_rate=self._client.in_sample_rate,
            )

    async def _on_participant_audio_data(
        self, participant_id: str, audio: AudioData, audio_source: str
    ):
        """Handle received participant audio data."""
        frame = InputAudioRawFrame(
            audio=audio.audio_frames,
            sample_rate=audio.sample_rate,
            num_channels=audio.num_channels,
        )
        frame.transport_source = audio_source
        await self.push_audio_frame(frame)


class BeyOutputTransport(BaseOutputTransport):
    """Output transport for sending audio and events to Bey conversations.

    Handles outgoing audio streams to participants and manages the custom
    audio track expected by the Beyond Presence platform. This transport
    resamples audio to 24kHz and chunks it to align with the 25fps video
    frame rate required by Beyond Presence.
    """

    def __init__(
        self,
        client: BeyTransportClient,
        params: TransportParams,
        daily_credentials_provider: DailyCredentialsProvider,
        **kwargs,
    ):
        """Initialize the Bey output transport.

        Args:
            client: The Bey transport client instance.
            params: Transport configuration parameters.
            room_url: Daily room URL for the session.
            daily_credentials_provider: Provider for Daily room URL and token.
            **kwargs: Additional arguments passed to parent class.
        """
        super().__init__(params, **kwargs)
        self._client = client
        self._params = params
        self._daily_credentials_provider = daily_credentials_provider

        # Whether we have seen a StartFrame already.
        self._initialized = False
        # This is the custom track destination expected by Bey
        self._transport_destination: str = "bey-custom-track"

        # Audio processing for Bey
        self._resampler = create_stream_resampler()
        self._out_sample_rate = 24000  # Bey requires 24kHz
        self._audio_buffer = bytearray()

    async def setup(self, setup: FrameProcessorSetup):
        """Setup the output transport.

        Args:
            setup: The frame processor setup configuration.
        """
        await super().setup(setup)
        room_url = self._daily_credentials_provider.get_url()
        token = await self._daily_credentials_provider.get_token()
        await self._client.setup(setup, room_url, token)

    async def cleanup(self):
        """Cleanup output transport resources."""
        await super().cleanup()
        await self._client.cleanup()

    async def start(self, frame: StartFrame):
        """Start the output transport.

        Args:
            frame: The start frame containing initialization parameters.
        """
        await super().start(frame)

        if self._initialized:
            return

        self._initialized = True

        await self._client.start(frame)

        if self._transport_destination:
            await self._client.register_audio_destination(self._transport_destination)

        await self.set_transport_ready(frame)

    async def stop(self, frame: EndFrame):
        """Stop the output transport.

        Args:
            frame: The end frame signaling transport shutdown.
        """
        await super().stop(frame)
        await self._client.stop()

    async def cancel(self, frame: CancelFrame):
        """Cancel the output transport.

        Args:
            frame: The cancel frame signaling immediate cancellation.
        """
        await super().cancel(frame)
        await self._client.stop()

    async def send_message(
        self, frame: OutputTransportMessageFrame | OutputTransportMessageUrgentFrame
    ):
        """Send a message to participants.

        Args:
            frame: The message frame to send.
        """
        logger.info(f"BeyOutputTransport sending message {frame}")
        await self._client.send_message(frame)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames and handle interruptions.

        Args:
            frame: The frame to process.
            direction: The direction of frame flow in the pipeline.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            await self._handle_interruptions()

    async def _handle_interruptions(self):
        """Handle interruption events by sending interrupt message."""
        await self._client.send_interrupt_message()

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        """Write an audio frame to the Bey transport.

        Resamples audio to 24kHz and chunks it to align with 25fps video frame rate
        before sending to the custom Bey audio track.

        Args:
            frame: The audio frame to write.

        Returns:
            True if the audio frame was written successfully, False otherwise.
        """
        in_sample_rate = frame.sample_rate
        chunk_size = int((self._out_sample_rate * 2) / FRAME_RATE)

        # Resample to 24kHz as required by Bey
        resampled = await self._resampler.resample(
            frame.audio, in_sample_rate, self._out_sample_rate
        )
        self._audio_buffer.extend(resampled)

        # Chunk audio to align with 25fps video frame rate
        success = True
        while len(self._audio_buffer) >= chunk_size:
            chunk = OutputAudioRawFrame(
                bytes(self._audio_buffer[:chunk_size]),
                sample_rate=self._out_sample_rate,
                num_channels=frame.num_channels,
            )

            # This is the custom track destination expected by Bey
            chunk.transport_destination = self._transport_destination

            self._audio_buffer = self._audio_buffer[chunk_size:]
            result = await self._client.write_audio_frame(chunk)
            success = success and result

        return success

    async def register_audio_destination(self, destination: str):
        """Register an audio destination.

        Args:
            destination: The destination identifier to register.
        """
        await self._client.register_audio_destination(destination)


class BeyTransport(BaseTransport):
    """Transport implementation for Beyond Presence avatar video calls.

    When used, the Pipecat bot joins the same virtual room as the Bey Avatar and the user.
    This is achieved by using `BeyTransportClient`, which initiates the session via the
    Beyond Presence API and connects to a Daily room where all participants meet.
    """

    def __init__(
        self,
        bot_name: str,
        session: aiohttp.ClientSession,
        bey_api_key: str,
        daily_api_key: str,
        avatar_id: str,
        room_url: str,
        params: BeyParams = BeyParams(),
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
    ):
        """Initialize the Bey transport.

        Args:
            bot_name: The name of the Pipecat bot.
            session: aiohttp session used for async HTTP requests.
            bey_api_key: Beyond Presence API key for authentication.
            daily_api_key: Daily API key for Daily services.
            avatar_id: ID of the avatar to use for video generation.
            room_url: Daily room URL for the session.
            params: Optional Bey-specific configuration parameters.
            input_name: Optional name for the input transport.
            output_name: Optional name for the output transport.
        """
        super().__init__(input_name=input_name, output_name=output_name)
        self._params = params
        self._bot_name = bot_name

        callbacks = BeyCallbacks(
            on_participant_joined=self._on_participant_joined,
            on_participant_left=self._on_participant_left,
        )
        self._client = BeyTransportClient(
            bot_name=bot_name,
            callbacks=callbacks,
            api_key=bey_api_key,
            avatar_id=avatar_id,
            session=session,
            params=params,
        )
        self._daily_credentials_provider = DailyCredentialsProvider(
            daily_rest_helper=DailyRESTHelper(
                daily_api_key=daily_api_key,
                aiohttp_session=session,
            ),
            room_url=room_url,
        )

        self._input: Optional[BeyInputTransport] = None
        self._output: Optional[BeyOutputTransport] = None
        self._bey_participant_id = None

        # Register supported handlers. The user will only be able to register
        # these handlers.
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    async def _on_participant_left(self, participant, reason):
        """Handle participant left events."""
        if participant.get("info", {}).get("userName", "") != _BEY_AVATAR_BOT_NAME:
            await self._on_client_disconnected(participant)

    async def _on_participant_joined(self, participant):
        """Handle participant joined events."""
        # Ignore the Bey avatar's microphone
        if participant.get("info", {}).get("userName", "") == _BEY_AVATAR_BOT_NAME:
            self._bey_participant_id = participant["id"]
        else:
            await self._on_client_connected(participant)
            if self._bey_participant_id:
                logger.debug(f"Ignoring {self._bey_participant_id}'s microphone")
                await self.update_subscriptions(
                    participant_settings={
                        self._bey_participant_id: {
                            "media": {"microphone": "unsubscribed"},
                        }
                    }
                )
            if self._input:
                await self._input.start_capturing_audio(participant)

    async def update_subscriptions(self, participant_settings=None, profile_settings=None):
        """Update subscription settings for participants.

        Args:
            participant_settings: Per-participant subscription settings.
            profile_settings: Global subscription profile settings.
        """
        await self._client.update_subscriptions(
            participant_settings=participant_settings,
            profile_settings=profile_settings,
        )

    def input(self) -> FrameProcessor:
        """Get the input transport for receiving media and events.

        Returns:
            The Bey input transport instance.
        """
        if not self._input:
            self._input = BeyInputTransport(
                client=self._client,
                params=self._params,
                daily_credentials_provider=self._daily_credentials_provider,
            )
        return self._input

    def output(self) -> FrameProcessor:
        """Get the output transport for sending media and events.

        Returns:
            The Bey output transport instance.
        """
        if not self._output:
            self._output = BeyOutputTransport(
                client=self._client,
                params=self._params,
                daily_credentials_provider=self._daily_credentials_provider,
            )
        return self._output

    async def _on_client_connected(self, participant: Any):
        """Handle client connected events."""
        await self._call_event_handler("on_client_connected", participant)

    async def _on_client_disconnected(self, participant: Any):
        """Handle client disconnected events."""
        await self._call_event_handler("on_client_disconnected", participant)
