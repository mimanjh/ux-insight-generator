"""
Hello Claude — minimal API test.

Confirms our API key works and we can get a response from Claude.
No screenshots, no logic, no error handling yet — just the bare wire.
"""

from dotenv import load_dotenv
from anthropic import Anthropic

# Load variables from .env into the process environment.
# After this line, os.environ has ANTHROPIC_API_KEY available.
load_dotenv()

# Create a client. The SDK looks for ANTHROPIC_API_KEY automatically,
# so we don't pass the key explicitly — safer (no risk of logging it).
client = Anthropic()

# Send one message to Claude and get one response back.
response = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=200,
    messages=[
        {"role": "user", "content": "Say hello in exactly one sentence."}
    ],
)

# The response object has a .content field which is a list of "blocks."
# For a simple text reply, there's one block, and its .text field is the answer.
print(response.content[0].text)