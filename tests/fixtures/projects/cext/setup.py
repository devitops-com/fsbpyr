from setuptools import Extension, setup

# libpthread: before glibc 2.34, pthread_create is there, not in libc
setup(ext_modules=[Extension("cext._native", ["src/cext/_native.c"], libraries=["pthread"])])
