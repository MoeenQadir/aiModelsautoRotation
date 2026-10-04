from setuptools import setup

setup(
    name='moonai',
    version='1.0.0',
    packages=['moon'],
    install_requires=[
        'pydantic',
        'pyyaml',
        'requests',
    ],
    python_requires='>=3.8',
)