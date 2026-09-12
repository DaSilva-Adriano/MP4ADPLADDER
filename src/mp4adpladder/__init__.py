"""MP4ADPLADDER — batch x265/MP4 ABR ladder encoder."""

__version__ = "1.0.0"


def main() -> None:
    from mp4adpladder.app import run

    run()
