import sys
from screenshot_tool.app import ScreenshotToolApp


def main():
    app = ScreenshotToolApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
