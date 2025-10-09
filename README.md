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

The `BeyVideoService` integrates between your TTS service and transport output:

```python
from pipecat.services.bey.video import BeyVideoService

bey_video = BeyVideoService(
    api_key=os.environ["BEY_API_KEY"],
    avatar_id="b9be11b8-89fb-4227-8f86-4a881393cbdb",  # Default "Ege" avatar
    bot_name="My Video Bot",
    transport_client=transport._client,
    rest_helper=daily_rest_helper,
    session=session,
)

pipeline = Pipeline([
    transport.input(),
    stt,
    context_aggregator.user(),
    llm,
    tts,
    bey_video,  # Add video service here
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
   python example.py --transport daily
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
