"""
MoonAI CLI Entrypoint
Enables `python -m moon "<prompt>" [--stream] [--reset-limits]` execution.
"""
import sys
import argparse
import asyncio
import logging
from dotenv import load_dotenv

# Ensure environment variables from .env are loaded
load_dotenv()

from .orchestrator import MoonOrchestrator
from .rate_limiter import RateLimiter, QuotaResetWorker


def main():
    parser = argparse.ArgumentParser(description="MoonAI Orchestrator CLI")
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text for the orchestrator task"
    )
    parser.add_argument(
        "--stream",
        "-s",
        action="store_true",
        help="Enable real-time SSE token streaming to terminal stdout"
    )
    parser.add_argument(
        "--reset-limits",
        "-r",
        nargs="?",
        const="ALL",
        default=None,
        help="Force reset rate limit counters (optionally specify provider name, e.g. --reset-limits groq)"
    )

    args = parser.parse_args()

    # Handle manual rate limit reset CLI command
    if args.reset_limits is not None:
        provider_arg = None if args.reset_limits == "ALL" else args.reset_limits
        rl = RateLimiter()
        count = rl.force_reset_provider_limits(provider_arg)
        target = f"provider '{provider_arg}'" if provider_arg else "all providers"
        print(f"[MoonAI] Force reset rate limit counters for {target}. ({count} entries cleared)")
        if not args.prompt:
            sys.exit(0)

    # Determine prompt text
    if args.prompt:
        prompt_text = " ".join(args.prompt)
    elif not sys.stdin.isatty():
        prompt_text = sys.stdin.read().strip()
    else:
        prompt_text = "Hello — confirm the MoonAI orchestrator is running"

    if not prompt_text:
        prompt_text = "Hello — confirm the MoonAI orchestrator is running"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.stream:
        print("\n[MoonAI SSE Streaming Execution Start]\n" + "-" * 40)

    orch = MoonOrchestrator(prompt=prompt_text, stream=args.stream)
    report = asyncio.run(orch.run())

    if args.stream:
        print("\n" + "-" * 40 + "\n[MoonAI SSE Streaming Execution End]")

    print("\n--- MoonAI execution report ---")
    print(f"status        : {report.get('status')}")
    print(f"task_id       : {report.get('task_id')}")
    print(f"selected_model: {report.get('selected_model')}")
    print(f"steps         : {len(report.get('steps', []))}")
    print(f"verification  : {report.get('verification_passed')}")
    print(f"failures      : {len(report.get('failures', []))}")
    print(f"content       : {report.get('final_content')}")
    print("---------------------------------\n")

    if report.get('status') in ('completed', 'success') or report.get('verification_passed'):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
