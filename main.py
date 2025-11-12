from pathlib import Path

from dellfans.server import run_server


def main() -> None:
    root_dir = Path(__file__).parent
    print("Starting fan controller on http://<server>:6180/dellfans (Ctrl+C to stop)")
    run_server(root_dir)


if __name__ == "__main__":
    main()
