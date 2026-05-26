from setuptools import setup, find_packages

setup(
    name="cua-collector",
    version="0.1.0",
    description="macOS dataset collector for Computer Use Agent (CUA) training",
    packages=find_packages(),
    install_requires=[
        "pyobjc>=10.2",
    ],
    python_requires=">=3.10",
)
