"""支持 ``python -m daily_report_agent``。"""

from .main import cli


if __name__ == "__main__":
    raise SystemExit(cli())
