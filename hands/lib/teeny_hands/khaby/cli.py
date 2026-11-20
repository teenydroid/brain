import argparse

from .. import hand


def main(argv):
    return hand.run("khaby", argparse.ArgumentParser(), argv)
