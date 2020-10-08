from setuptools import find_packages, setup


setup(
    name='operator-interface-nrpe-external-master',
    packages=find_packages(include=['nrpe_external_master']),
    version='0.0.1',
    license='MIT',
    long_description=open('README.md', 'r').read(),
    url='https://github.com/omnivector-solutions/operator-interface-nrpe-external-master',
    python_requires='>=3.6',
)
