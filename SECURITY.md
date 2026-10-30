# Security policy

OrderFlow is a portfolio backend with a mock payment provider. It is not a reviewed banking integration or a ready-made public authentication service. The current release series is `0.1.x`.

Please report vulnerabilities through [GitHub private vulnerability reporting](https://github.com/Sasambatra/orderflow/security/advisories/new), rather than publishing exploit details or credentials in an issue. Include the affected revision, prerequisites and a minimal reproduction with synthetic data. There is no guaranteed response time or bug bounty.

Development credentials are generated locally and excluded from Git. Do not expose the development Compose stack publicly. A real deployment needs TLS, managed secrets, account recovery/email verification, operational monitoring and tested backup/restore.
