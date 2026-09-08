from __future__ import annotations

from pathlib import Path

from setuptools import find_packages, setup

PROJECT_ROOT = Path(__file__).resolve().parent

setup(
    name="BusLens",
    version="1.0.0",
    description="Monitor e Inspector de Protocolos de Hardware / USB para Windows 10/11",
    long_description=(PROJECT_ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    author="BusLens",
    license="MIT",
    packages=find_packages(include=["BusLens", "BusLens.*"]),
    python_requires=">=3.9",
    install_requires=[
        "wmi>=1.5.0; sys_platform == 'win32'",
        "pywin32>=306; sys_platform == 'win32'",
        # Solo los namespaces PyWinRT que BusLens importa (nada de [all],
        # que arrastra toda la proyección WinRT y eterniza el CI).
        "winrt-windows.system>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.text>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.viewmanagement>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.xaml>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.xaml.controls>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.xaml.data>=1.0.0; sys_platform == 'win32'",
        "winrt-windows.ui.xaml.media>=1.0.0; sys_platform == 'win32'",
    ],
    entry_points={
        "console_scripts": [
            "buslens = buslens.__main__:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Win32 (MS Windows)",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: System :: Hardware",
        "Topic :: System :: Monitoring",
    ],
)