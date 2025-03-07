from setuptools import setup, find_packages

setup(
    name='QPs-settings-manager',
    version='0.1',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    install_requires=[
        'PyQt5',
        'Pillow',
        'requests', 
        'sqlalchemy',  
        'pymysql', 
        'psycopg2', 
        'pytest',  
        'coverage',  
        'sphinx',  
        'sphinx_rtd_theme', 
        'wheel', 
        'setuptools',
        'pyinstaller',  
    ],
    entry_points={
        'console_scripts': [
            'graphics-settings-manager=main:main', 
        ],
    },
    author='Dakota Naylor',
    author_email='dakota.naylor@protonmail.com',
    description='A lightweight GPU settings manager designed for use with handheld devices.',
    license='MIT',
    keywords='graphics settings manager',
    url='https://github.com/QPfixxy/QPs-Graphics-Manager',  
)