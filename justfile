set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

test:
    python3 -m unittest discover -s tests -p 'test_*.py'

test-unit:
    python3 -m unittest tests.test_snap_packaging

test-lxd:
    python3 -m unittest tests.test_lxd_snap

check: test

pack:
    snapcraft pack --use-lxd

pack-destructive:
    snapcraft pack --destructive-mode

pack-test-lxd: pack test-lxd

try:
    snapcraft try

clean:
    rm -rf parts prime stage .snapcraft *.snap
