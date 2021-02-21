# coding: utf-8

from __future__ import with_statement

from setuptools import setup


def get_readme() -> str:
    with open("README.md") as readme_handle:
        return readme_handle.read()


setup(
    name="on_air",
    version="0.1.0",
    description="Show a warning light when someone in the room is on-air",
    long_description=get_readme(),
    long_description_content_type="text/markdown",
    keywords="audio video warning iot blink light indicator",
    author="Tom Milligan",
    author_email="code@tommilligan.net",
    url="https://github.com/tommilligan/on-air",
    license="MIT",
    packages=["on_air"],
    install_requires=[],
    zip_safe=False,
    entry_points={"console_scripts": ["on-air=on_air:main"]},
    classifiers=[
        "Operating System :: POSIX :: Linux",
        "License :: OSI Approved :: MIT",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
)
