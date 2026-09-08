from __future__ import annotations

from setuptools import setup, find_packages

setup(
    name="buslens",
    version="1.0.0",
    description="Monitor e Inspector de Protocolos de Hardware / USB para Windows 10/11",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    author="BusLens",
    license="MIT",
    packages=find_packages(include=["buslens", "buslens.*"]),
    python_requires=">=3.9",
    install_requires=[
        "wmi>=1.5.0",
        "pywin32>=306",
        "winrt-Windows>=10.0.22621",
    ],
    entry_points={
        "console_scripts": [
            "buslens = BusLens.__main__:main",
        ],
    },
)
