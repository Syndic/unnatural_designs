package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	audit "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/audit"
)

const (
	defaultBaseURL    = "http://mini.dev.yanch.ar:8000"
	defaultTokenFile  = ".netbox_api_token" //nolint:gosec // this is a filename, not a hardcoded value
	defaultConfigFile = "netbox_audit.config.json"
	defaultMaxAttempt = 5
)

type auditConfig struct {
	Rules  auditRules   `json:"rules"`
	Checks checksConfig `json:"checks"`
}

type auditRules struct {
	InterfaceVRF          audit.InterfaceVRFRules          `json:"interface-vrf"`
	PrivateIPVRF          audit.PrivateIPVRFRules          `json:"private-ip-vrf"`
	WirelessNormalization audit.WirelessNormalizationRules `json:"wireless-normalization"`
	RackPlacement         audit.RackPlacementRules         `json:"rack-placement"`
	PoEPower              audit.POEPowerRules              `json:"poe-power"`
}

type checksConfig struct {
	Enabled  []string `json:"enabled"`
	Disabled []string `json:"disabled"`
}

func defaultAuditConfig() auditConfig {
	return auditConfig{
		Rules: auditRules{
			InterfaceVRF: audit.InterfaceVRFRules{
				WANDeviceRoles:      []string{"ISP Equipment"},
				RequireOnInterfaces: true,
			},
			PrivateIPVRF: audit.PrivateIPVRFRules{
				RequireOnPrivateIPs: true,
				RequireOnPublicIPs:  false,
			},
			WirelessNormalization: audit.WirelessNormalizationRules{
				SuppressIfConnectedWiredInterfaceIsComplete: true,
				RequireMode:         true,
				RequireUntaggedVLAN: true,
				RequirePrimaryMAC:   true,
			},
			RackPlacement: audit.RackPlacementRules{
				ExemptChildDevices: true,
				ExemptDeviceTags:   []string{"0u-rack-device"},
			},
			PoEPower: audit.POEPowerRules{
				CheckPoweredDeviceSupply: true,
				RequirePSEModeOnPeer:     true,
				UnknownTypePolicy:        audit.POEUnknownTypeFail,
			},
		},
	}
}

func loadAuditConfig(path string, required bool) (auditConfig, error) {
	cfg := defaultAuditConfig()
	if strings.TrimSpace(path) == "" {
		return cfg, nil
	}
	data, err := os.ReadFile(path) //nolint:gosec // Yeah, we're loading a config file.
	if err != nil {
		if os.IsNotExist(err) && !required {
			return cfg, nil
		}
		return auditConfig{}, err
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return auditConfig{}, err
	}
	switch strings.ToLower(strings.TrimSpace(cfg.Rules.PoEPower.UnknownTypePolicy)) {
	case "", audit.POEUnknownTypeFail:
		cfg.Rules.PoEPower.UnknownTypePolicy = audit.POEUnknownTypeFail
	case audit.POEUnknownTypeIgnore:
		cfg.Rules.PoEPower.UnknownTypePolicy = audit.POEUnknownTypeIgnore
	default:
		return auditConfig{}, fmt.Errorf("unsupported poe-power.unknown_type_policy %q", cfg.Rules.PoEPower.UnknownTypePolicy)
	}
	return cfg, nil
}

func defaultConfigPath() string {
	if cwd, err := os.Getwd(); err == nil {
		candidates := []string{
			filepath.Join(cwd, defaultConfigFile),
			filepath.Join(cwd, "..", "..", defaultConfigFile),
		}
		for _, candidate := range candidates {
			if _, err := os.Stat(candidate); err == nil {
				return candidate
			}
		}
		return ""
	}
	return ""
}

func envOrDefault(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
