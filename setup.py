# setup.py
from setuptools import setup, find_packages

setup(
    name="blitspersecond",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "pillow",
        "pyglet @ git+https://github.com/pyglet/pyglet.git@d074003ffc033f4a4ac67a749794e4cb77a7a368"
    ],
    extras_require={
        "dev": [
            "pytest",
            "pytest-cov",
            "black",
            "mypy"
        ]
    },
    python_requires=">=3.8",
    include_package_data=True,
    package_data={"": ["img/*.png"]},
    author="Space Channel 5",
    description="BlitsPerSecond Game Engine",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/blitspersecond/blitspersecond",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
