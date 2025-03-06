from setuptools import setup, find_packages

setup(
    name='graphics-settings-manager',
    version='0.1',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    install_requires=[
        # List dependencies here, e.g.:
        # 'PyQt5',
        # 'Pillow',
    ],
    entry_points={
        'console_scripts': [
            'graphics-settings-manager=main:main',  # Adjust according to your main function
        ],
    },
    author='Your Name',
    author_email='your.email@example.com',
    description='A simple GUI program for managing graphics settings for video games.',
    license='MIT',
    keywords='graphics settings manager',
    url='https://github.com/yourusername/graphics-settings-manager',  # Update with your repository URL
)