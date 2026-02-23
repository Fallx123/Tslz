"""Run the Hynous daemon as a standalone process."""

from hynous.intelligence.daemon import run_standalone


def main() -> None:
    run_standalone()


if __name__ == "__main__":
    main()
