#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import os
import sys

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.daily.utils import (
    DailyMeetingTokenParams,
    DailyMeetingTokenProperties,
    DailyRESTHelper,
)

from pipecat_bey.transport import BeyParams, BeyTransport

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

# Ege stock avatar
# Ref: https://docs.bey.dev/get-started/avatars/default
AVATAR_ID = "b9be11b8-89fb-4227-8f86-4a881393cbdb"


async def main():
    async with aiohttp.ClientSession() as session:
        room_url = os.environ["DAILY_ROOM_URL"]

        transport = BeyTransport(
            bot_name="Pipecat bot",
            session=session,
            bey_api_key=os.environ["BEY_API_KEY"],
            daily_api_key=os.environ["DAILY_API_KEY"],
            avatar_id=AVATAR_ID,
            room_url=room_url,
            params=BeyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                microphone_out_enabled=False,
                vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            ),
        )

        stt = OpenAISTTService(api_key=os.environ["OPENAI_API_KEY"])

        tts = OpenAITTSService(api_key=os.environ["OPENAI_API_KEY"])

        llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"])

        messages = [
            {
                "role": "system",
                "content": "You are a helpful LLM in a WebRTC call. Your goal is to demonstrate your capabilities in a succinct way. Your output will be converted to audio so don't include special characters in your answers. Respond to what the user said in a creative and helpful way.",
            },
        ]

        context = OpenAILLMContext(messages)
        context_aggregator = llm.create_context_aggregator(context)

        pipeline = Pipeline(
            [
                transport.input(),  # Transport user input
                stt,  # STT
                context_aggregator.user(),  # User responses
                llm,  # LLM
                tts,  # TTS
                transport.output(),  # Transport bot output
                context_aggregator.assistant(),  # Assistant spoken responses
            ]
        )

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                audio_in_sample_rate=16000,
                audio_out_sample_rate=24000,
                enable_metrics=True,
                enable_usage_metrics=True,
            ),
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, participant):
            logger.info(f"Client connected")
            # Kick off the conversation.
            messages.append(
                {
                    "role": "system",
                    "content": "Start by greeting the user and ask how you can help.",
                }
            )
            await task.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, participant):
            logger.info(f"Client disconnected")
            await task.cancel()

        runner = PipelineRunner()

        await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
