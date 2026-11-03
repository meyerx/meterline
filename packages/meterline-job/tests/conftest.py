def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs the real simulator and about six real gold builds (a few minutes)")
