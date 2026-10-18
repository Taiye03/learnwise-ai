import os
from dotenv import load_dotenv
from anthropic import Anthropic

# Load API key from .env
load_dotenv(override=True)

# Create Claude client
client = Anthropic(
    api_key=os.environ["ANTHROPIC_API_KEY"]
)

# Send a message to Claude
response = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=150,
    messages=[
        {
            "role": "user",
            "content": "Explain fractions to an 8-year-old in two sentences."
        }
    ]
)

# Display Claude's response
print(response.content[0].text)