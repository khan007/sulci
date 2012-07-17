#!/usr/bin/env python

import unittest
import argparse

from sulci.tests import textutils, sample, token


if __name__ == "__main__":

    # Define arguments
    parser = argparse.ArgumentParser(description="Run sulci tests suite.")
    parser.add_argument(
        "tests",
        nargs="*",
        default=None,
        help="Tests (module, TestCase or TestCaseMethod) to run (use full path, eg. module.Class.test)."
    )
    parser.add_argument(
        "-v",
        "--verbosity",
        type=int,
        action="store",
        dest="verbosity",
        default=2,
        help="Verbosity of the runner."
    )
    args = parser.parse_args()

    if args.tests:
        # we have names
        suite = unittest.TestLoader().loadTestsFromNames(args.tests)
    else:
        # Run all the tests
        suites = []
        for mod in [textutils, sample, token]:
            suite = unittest.TestLoader().loadTestsFromModule(mod)
            suites.append(suite)
        suite = unittest.TestSuite(suites)
    unittest.TextTestRunner(verbosity=args.verbosity).run(suite)
