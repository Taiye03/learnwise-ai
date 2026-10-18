import oci

config = oci.config.from_file()
oci.config.validate_config(config)

print("OCI configuration loaded successfully!")
print("Region:", config["region"])