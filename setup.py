from setuptools import setup, find_packages

setup(
    name="cligoo",
    version="1.0.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "httpx>=0.25",
        "rich>=13.0",
        "click>=8.0",
        "keyring>=24.0",
        "humanize>=4.0",
        "pyjwt>=2.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0", "pytest-cov", "ruff"],
    },
    entry_points={
        "console_scripts": ["cligoo=cligoo.cli:main"],
    },
    python_requires=">=3.9",
)
