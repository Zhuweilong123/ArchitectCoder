"""Call-site policies; facts and rules never approve reviews or commits."""


def allows_design_review(report):
    return not report.has_errors


# Contract commit policy is owned by the design-contract extension. The shared
# engine returns coverage and diagnostics; it never authorizes a transaction.
