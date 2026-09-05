# Used to test that a real image size increase is detected. /dev/urandom (not
# /dev/zero) is required here: an all-zero file is stored as a sparse region by the
# overlay filesystem and barely adds to the reported image size.
FROM alpine:3.20
RUN dd if=/dev/urandom of=/extra-data bs=1024 count=1024
