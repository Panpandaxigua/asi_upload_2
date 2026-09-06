# -*- coding: utf-8 -*-
"""
ASI Product Upload command entry.

Examples:
    python main.py
    python main.py --yes
    python main.py --excel "F:\\asi_product_upload\\ASI表格.xlsx" --image-root "F:\\asi_product_upload"
"""
import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ASI_ID = "65991"
DEFAULT_USERNAME = "info@llncustom.us"
DEFAULT_PASSWORD = "Lln147258369!!"
DEFAULT_EXCEL_PATH = Path(__file__).with_name("ASI表格.xlsx")


@dataclass(frozen=True)
class UploadConfig:
    excel_path: Path
    asi_id: str
    username: str
    password: str
    image_root_path: Path | None
    assume_yes: bool
    distributor_only_view: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload products from an Excel file to ASI.",
    )
    parser.add_argument(
        "--excel",
        default=str(DEFAULT_EXCEL_PATH),
        help="Excel file path. Default: ASI表格.xlsx beside main.py.",
    )
    parser.add_argument(
        "--asi-id",
        default=os.getenv("ASI_ID", DEFAULT_ASI_ID),
        help="ASI number. Can also be set with ASI_ID.",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("ASI_USERNAME", DEFAULT_USERNAME),
        help="ASI login username. Can also be set with ASI_USERNAME.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("ASI_PASSWORD", DEFAULT_PASSWORD),
        help="ASI login password. Can also be set with ASI_PASSWORD.",
    )
    parser.add_argument(
        "--distributor-only-view",
        action="store_true",
        help="Hide uploaded products from end-buyer sites.",
    )
    parser.add_argument(
        "--image-root",
        default=None,
        help="Image root directory. Defaults to the Excel file directory.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Start without interactive confirmation.",
    )
    return parser


def parse_config(argv: list[str] | None = None) -> UploadConfig:
    args = build_parser().parse_args(argv)
    excel_path = Path(args.excel).expanduser().resolve()
    image_root_path = Path(args.image_root).expanduser().resolve() if args.image_root else None
    return UploadConfig(
        excel_path=excel_path,
        asi_id=str(args.asi_id).strip(),
        username=str(args.username).strip(),
        password=str(args.password),
        image_root_path=image_root_path,
        assume_yes=bool(args.yes),
        distributor_only_view=bool(args.distributor_only_view),
    )


def validate_config(config: UploadConfig) -> list[str]:
    errors = []
    if not config.excel_path.exists():
        errors.append(f"Excel file does not exist: {config.excel_path}")
    if config.image_root_path and not config.image_root_path.exists():
        errors.append(f"Image root directory does not exist: {config.image_root_path}")
    if not config.asi_id:
        errors.append("ASI ID is empty.")
    if not config.username:
        errors.append("Username is empty.")
    if not config.password:
        errors.append("Password is empty.")
    return errors


def print_banner(config: UploadConfig) -> None:
    print("=" * 60)
    print("  ASI Product Upload - 产品自动上传工具")
    print("=" * 60)
    print(f"  Excel: {config.excel_path}")
    print(f"  图片目录: {config.image_root_path or config.excel_path.parent}")
    print(f"  ASI ID: {config.asi_id}")
    print(f"  账号: {config.username}")
    print()


def confirm_start(config: UploadConfig) -> bool:
    if config.assume_yes:
        return True
    answer = input("  是否开始上传? (y/n): ").strip().lower()
    return answer == "y"


def main(argv: list[str] | None = None) -> int:
    config = parse_config(argv)
    print_banner(config)

    errors = validate_config(config)
    if errors:
        for error in errors:
            print(f"  配置错误: {error}")
        return 2

    if not confirm_start(config):
        print("  已取消")
        return 0

    from asi_bot import run_upload

    run_upload(
        str(config.excel_path),
        config.asi_id,
        config.username,
        config.password,
        image_root_path=str(config.image_root_path) if config.image_root_path else None,
        distributor_only_view=config.distributor_only_view,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
