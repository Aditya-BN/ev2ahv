.PHONY: appliance appliance-smoke appliance-scan
appliance:
	appliance/build.sh
appliance-smoke:
	appliance/scripts/smoke.sh
appliance-scan:
	appliance/scripts/scan.sh
