import asyncio
import json
import os


from pika_flow.llm_manager import LLMManager
from pika_flow.logger import PikaLogger

async def test():
    log = PikaLogger()
    llm = LLMManager(log)
    
    # 1. Test Gemini
    print("Testing Gemini...")
    try:
        res = await llm._do_request(
            llm._providers[0],
            {"contents": [{"parts": [{"text": "Hello"}]}]}
        )
        print("Gemini success:", str(res)[:100])
    except Exception as e:
        print("Gemini failed:", e)

    # 2. Test Groq
    print("Testing Groq...")
    try:
        res = await llm._do_request(
            llm._providers[1],
            {"contents": [{"parts": [{"text": "Hello"}]}]}
        )
        print("Groq success:", str(res)[:100])
    except Exception as e:
        print("Groq failed:", e)

    # 3. Test OpenRouter
    print("Testing OpenRouter...")
    try:
        res = await llm._do_request(
            llm._providers[2],
            {"contents": [{"parts": [{"text": "Hello"}]}]}
        )
        print("OpenRouter success:", str(res)[:100])
    except Exception as e:
        print("OpenRouter failed:", e)

    await llm.close()

if __name__ == "__main__":
    asyncio.run(test())
