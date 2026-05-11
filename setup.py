from setuptools import setup, find_packages

setup(
    name="technical-bot",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "lumibot",
        "pandas",
        "numpy",
        "python-dotenv",
        "yfinance",
        "pandas-ta",
        "alpaca-trade-api",
        "alpaca-py",
	"requests",
	"pytz",
    ],
)