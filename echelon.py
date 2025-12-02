import argparse
import sys
import getpass
from typing import Optional

from config_manager import ConfigManager
from github_client import GitHubClient
from models import SnippetConfig
from monitor import SnippetMonitor
from notifier import DiscordNotifier
from utils import snippet_id_from_parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Echelon - Monitor specific GitHub file line ranges and notify via Discord with AI."
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--add",
        help='Add a new snippet to monitor. Example: "https://github.com/owner/repo/blob/main/path/file.py#L26-L31"',
    )
    group.add_argument(
        "--remove",
        help='Remove a snippet from monitoring by its URL. Example: "https://github.com/owner/repo/blob/main/file.js#L52-L64"',
    )

    parser.add_argument("--note", help="Custom note describing why this snippet is important. Used with --add.")
    parser.add_argument("--time", type=int, help="Polling interval (seconds) for the daemon.")
    parser.add_argument("--ai", help="AI provider to use for diff summaries: gemini | openai | ollama")
    parser.add_argument("--model", help="Model name for the selected provider.")
    parser.add_argument("--run", action="store_true", help="Start monitoring daemon.")
    parser.add_argument("--init", action="store_true", help="Interactively prompt to add missing API keys / Discord webhook")

    return parser


def prompt_if_missing(config_manager: ConfigManager) -> None:
    config = config_manager.load()
    changed = False

    print("Interactive configuration initialization. Press Enter to skip!.\n")

    if not config.webhook_url:
        val = input("Discord webhook URL: ").strip()
        if val:
            config.webhook_url = val
            changed = True
            print("Saved webhook_url to config.json")
    else:
        print("webhook_url already set in config.json")

    if not config.openai_key:
        val = getpass.getpass("OpenAI API key [skip]: ").strip()
        if val:
            config.openai_key = val
            changed = True
            print("Saved openai_key to config.json")
    else:
        print("openai_key already set!")

    if not config.openai_model:
        val = input("Default OpenAI model (e.g. gpt-4o-mini) [skip]: ").strip()
        if val:
            config.openai_model = val
            changed = True
            print("Saved openai_model to config.json")
    else:
        print("openai_model already set in config.json")

    if not config.gemini_api_key:
        val = getpass.getpass("Gemini API key [skip]: ").strip()
        if val:
            config.gemini_api_key = val
            changed = True
            print("Saved gemini_api_key to config.json")
    else:
        print("gemini_api_key already set in config.json")

    if not config.gemini_model:
        val = input("Default Gemini model (e.g. gemini-2.5-flash) [skip]: ").strip()
        if val:
            config.gemini_model = val
            changed = True
            print("Saved gemini_model to config.json")
    else:
        print("gemini_model already set in config.json")

    if not config.ollama_endpoint:
        val = input("Ollama endpoint (e.g. http://localhost:11434) [skip]: ").strip()
        if val:
            config.ollama_endpoint = val
            changed = True
            print("Saved ollama_endpoint to config.json")
    else:
        print("ollama_endpoint already set in config.json")

    if not config.ollama_model:
        val = input("Default Ollama model (e.g. llama3.1:405b) [skip]: ").strip()
        if val:
            config.ollama_model = val
            changed = True
            print("Saved ollama_model to config.json")
    else:
        print("ollama_model already set in config.json")

    if not config.interval_seconds:
        pass

    if changed:
        config_manager.save(config)
        print("\nConfiguration updated and saved to config.json.")
    else:
        print("\nNo changes made to config.json.")


def handle_add(args, config_manager: ConfigManager, github_client: GitHubClient) -> None:
    if not args.add:
        return

    config = config_manager.load()
    parsed = github_client.parse_github_url(args.add)
    snippet_id = snippet_id_from_parsed(parsed)

    existing = config.find_snippet_by_url(parsed.file_url)
    if existing:
        print(f"Snippet already configured: {parsed.file_url}")
        return

    content = github_client.fetch_file_content(parsed)
    snippet_text = github_client.extract_lines(content, parsed.start_line, parsed.end_line)

    new_snippet = SnippetConfig(
        id=snippet_id,
        owner=parsed.owner,
        repo=parsed.repo,
        branch=parsed.branch,
        file_path=parsed.file_path,
        start_line=parsed.start_line,
        end_line=parsed.end_line,
        file_url=parsed.file_url,
        note=args.note or "",
        original_code=snippet_text,
        last_seen_code=snippet_text,
    )

    if config.snippets is None:
        config.snippets = []
    config.snippets.append(new_snippet)
    config_manager.save(config)

    print("Added snippet to config.json (monitoring will start when you run with --run):")
    print(f"  {parsed.file_url}")
    if new_snippet.note:
        print(f"  Note: {new_snippet.note}")


def handle_remove(args, config_manager: ConfigManager) -> None:
    if not args.remove:
        return
    config = config_manager.load()
    removed = config.remove_snippet_by_url(args.remove)
    if removed:
        config_manager.save(config)
        print(f"Removed snippet: {args.remove}")
    else:
        print(f"No matching snippet found for: {args.remove}")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    config_manager = ConfigManager()
    github_client = GitHubClient()
    notifier = DiscordNotifier()
    config = config_manager.load()

    if args.init:
        prompt_if_missing(config_manager)
        config = config_manager.load()

    if args.time is not None:
        if args.time <= 0:
            print("--time must be a positive integer")
            sys.exit(1)
        config.interval_seconds = args.time
        config_manager.save(config)

    if args.add:
        handle_add(args, config_manager, github_client)
        return

    if args.remove:
        handle_remove(args, config_manager)
        return

    if not args.run:
        parser.print_help()
        return

    config = config_manager.load()

    if not config.snippets:
        print("No snippets configured in config.json. Add at least one with --add before running daemon.")
        return

    if not config.webhook_url:
        print("No Discord webhook configured in config.json. Run with --init to add values interactively, or edit config.json.")
        return

    provider = args.ai.lower() if args.ai else None
    if provider:
        if provider == "openai":
            if not config.openai_key:
                print("OpenAI provider selected but openai_key is missing in config.json. Run with --init to add it.")
                return
        elif provider == "gemini":
            if not config.gemini_api_key:
                print("Gemini provider selected but gemini_api_key is missing in config.json. Run with --init to add it.")
                return
        elif provider == "ollama":
            if not config.ollama_endpoint:
                print("Ollama provider selected but ollama_endpoint is missing in config.json. Run with --init to add it.")
                return
        else:
            print("--ai must be one of: gemini | openai | ollama")
            return

    run_model = args.model or None

    monitor = SnippetMonitor(
        config_manager=config_manager,
        github_client=github_client,
        notifier=notifier,
        debug=True,
        provider=provider,
        model=run_model,
    )

    print("Starting monitoring daemon... Press Ctrl+C to stop.")
    monitor.run_forever()


if __name__ == "__main__":
    main()
