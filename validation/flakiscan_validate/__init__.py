"""Build-based validation for a pair of Dockerfiles: an original and a modified copy.

This package is deliberately independent of flakiscan: it takes two Dockerfile paths
and a build context, and reports whether the modified version still builds. It does
not import anything from flakiscan and does not know what produced either file --
flakiscan's automated repairs are one way to produce a "patched" Dockerfile worth
validating this way, but any other source works equally well.

This is also the only place in the two packages that invokes `docker build`.
"""
