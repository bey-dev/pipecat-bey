# Pipecat Beyond Presence Integration

Generate real-time video avatars for your Pipecat AI agents with [Beyond Presence](https://beyondpresence.ai).

**Maintainer:** Beyond Presence team ([@bey-dev](https://github.com/bey-dev))

## Installation

```bash
pip install pipecat-ai-bey
```

## Prerequisites

- [Beyond Presence API key](https://beyondpresence.ai)
- [Daily.co API key](https://www.daily.co/)
- API keys for STT/TTS/LLM services (e.g., OpenAI)

## Usage with Pipecat Pipeline

The `BeyTransport` integrates with the Beyond Presence platform to create conversational AI applications where a Beyond Presence avatar provides synchronized video and audio output while your bot handles the conversation logic.

```python
from pipecat_bey import BeyParams, BeyTransport

transport = BeyTransport(
    bot_name="Pipecat bot",
    session=session,
    bey_api_key=os.environ["BEY_API_KEY"],
    daily_api_key=os.environ["DAILY_API_KEY"],
    avatar_id="b9be11b8-89fb-4227-8f86-4a881393cbdb",  # Default "Ege" avatar
    room_url=os.environ["DAILY_ROOM_URL"],
    params=BeyParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        microphone_out_enabled=False,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
    ),
)

pipeline = Pipeline([
    transport.input(),
    stt,
    context_aggregator.user(),
    llm,
    tts,
    transport.output(),
    context_aggregator.assistant(),
])
```

See [example.py](example.py) for a complete working example.

## Running the Example

1. Install dependencies:
   ```bash
   uv sync
   ```

2. Set up your environment

   ```bash
   cp env.example .env
   ```

3. Run:
   ```bash
   uv run python example.py
   ```

The bot will create a Daily room with a video avatar that responds to your voice.

## Compatibility

**Tested with Pipecat v0.0.89**

- Python 3.10+
- Daily transport (generic WebRTC support coming soon)

## License

BSD-2-Clause - see [LICENSE](LICENSE)

## Support

- Docs: https://docs.bey.dev
- Pipecat Discord: https://discord.gg/pipecat (`#community-integrations`)
