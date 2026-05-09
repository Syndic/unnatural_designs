package audit

import (
	"reflect"
	"strings"
	"testing"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

// indexSnapshot mirrors netbox.Snapshot.buildIndexes (which is unexported).
func indexSnapshot(s *netbox.Snapshot) {
	s.DevicesByID = map[int]netbox.Device{}
	for _, d := range s.Devices {
		s.DevicesByID[d.ID] = d
	}
	s.InterfacesByID = map[int]netbox.Iface{}
	s.InterfacesByDevice = map[int][]netbox.Iface{}
	for _, it := range s.Interfaces {
		s.InterfacesByID[it.ID] = it
		s.InterfacesByDevice[it.Device.ID] = append(s.InterfacesByDevice[it.Device.ID], it)
	}
	s.IPsByInterface = map[int][]netbox.IPAddress{}
	for _, ip := range s.IPAddresses {
		if ip.AssignedObjectType == netbox.ObjectTypeInterface {
			s.IPsByInterface[ip.AssignedObjectID] = append(s.IPsByInterface[ip.AssignedObjectID], ip)
		}
	}
	s.ModuleBaysByID = map[int]netbox.ModuleBay{}
	for _, mb := range s.ModuleBays {
		s.ModuleBaysByID[mb.ID] = mb
	}
}

func choice(v string) netbox.Choice           { return netbox.Choice{Value: v, Label: v} }
func choicePtr(v string) *netbox.Choice       { c := choice(v); return &c }
func namedRef(id int, name string) netbox.NamedRef { return netbox.NamedRef{ID: id, Name: name} }

func TestCables(t *testing.T) {
	s := netbox.Snapshot{Cables: []netbox.Cable{
		{ID: 1, Type: "cat6", Status: choice("connected"), ATerminations: []netbox.Termination{{}}, BTerminations: []netbox.Termination{{}}},
		{ID: 2, Status: choice("connected"), ATerminations: []netbox.Termination{{}}, BTerminations: []netbox.Termination{{}}},
		{ID: 3, Type: "cat6", ATerminations: []netbox.Termination{{}}, BTerminations: []netbox.Termination{{}}},
		{ID: 4, Type: "cat6", Status: choice("connected"), ATerminations: []netbox.Termination{{}}},
		{ID: 5, Type: "cat6", Status: choice("connected")},
	}}
	got := Cables(s)
	if len(got.Findings) != 4 {
		t.Fatalf("expected 4 findings, got %d: %v", len(got.Findings), got.Findings)
	}
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{"#2 is missing type", "#3 is missing status", "#4 is missing a termination on side B", "#5 is missing a termination on side A+B"} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q in findings: %v", want, got.Findings)
		}
	}
}

func TestCablesClean(t *testing.T) {
	s := netbox.Snapshot{Cables: []netbox.Cable{
		{ID: 1, Type: "cat6", Status: choice("connected"), ATerminations: []netbox.Termination{{}}, BTerminations: []netbox.Termination{{}}},
	}}
	got := Cables(s)
	if len(got.Findings) != 0 {
		t.Fatalf("expected no findings, got %v", got.Findings)
	}
}

func TestDeviceLocations(t *testing.T) {
	s := netbox.Snapshot{Devices: []netbox.Device{
		{Name: "a", Location: &netbox.NamedRef{ID: 1, Name: "rack1"}},
		{Name: "b"},
	}}
	got := DeviceLocations(s)
	if len(got.Findings) != 1 || !strings.Contains(got.Findings[0], "b is missing location") {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestDHCPReservations(t *testing.T) {
	s := netbox.Snapshot{
		IPRanges: []netbox.IPRange{
			{ID: 1, StartAddress: "10.0.0.10/24", EndAddress: "10.0.0.20/24", Tags: []netbox.TagRef{{Slug: TagDHCPReserved}}},
			{ID: 2, StartAddress: "10.0.0.15/24", EndAddress: "10.0.0.30/24", Tags: []netbox.TagRef{{Slug: TagDHCPPool}}},
		},
		Interfaces: []netbox.Iface{
			{ID: 100, Name: "eth0", Device: namedRef(1, "host"), MACAddress: "aa:bb:cc:dd:ee:ff"},
			{ID: 101, Name: "eth1", Device: namedRef(1, "host")},
		},
		IPAddresses: []netbox.IPAddress{
			// Reserved IP, properly assigned, in range, with MAC -> no finding.
			{ID: 1, Address: "10.0.0.12/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 100, Tags: []netbox.TagRef{{Slug: TagDHCPReserved}}},
			// Not assigned to interface.
			{ID: 2, Address: "10.0.0.13/24", AssignedObjectType: "", Tags: []netbox.TagRef{{Slug: TagDHCPReserved}}},
			// Assigned to missing interface.
			{ID: 3, Address: "10.0.0.14/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 9999, Tags: []netbox.TagRef{{Slug: TagDHCPReserved}}},
			// Not in any reserved range AND iface has no MAC.
			{ID: 4, Address: "10.0.0.99/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 101, Tags: []netbox.TagRef{{Slug: TagDHCPReserved}}},
		},
	}
	indexSnapshot(&s)
	got := DHCPReservations(s)
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"Range overlap",
		"is not assigned to an interface",
		"assigned interface 9999 was not loaded",
		"is not inside any dhcp-reserved range",
		"has no unambiguous MAC",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q in findings:\n%s", want, joined)
		}
	}
}

func TestDeviceTypeDriftHappyPath(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{{ID: 1, Name: "sw1", DeviceType: netbox.DeviceTypeRef{ID: 10, Model: "model"}}},
		InterfaceTemplates: []netbox.InterfaceTemplate{
			{Name: "eth0", DeviceType: &netbox.IDRef{ID: 10}, Type: choice("1000base-t"), Enabled: true},
		},
		Interfaces: []netbox.Iface{
			{ID: 100, Name: "eth0", Device: namedRef(1, "sw1"), Type: choice("1000base-t"), Enabled: true},
		},
	}
	got := DeviceTypeDrift(s)
	if len(got.Findings) != 0 || len(got.Extra) != 0 {
		t.Fatalf("expected no drift, got findings=%v extra=%v", got.Findings, got.Extra)
	}
}

func TestDeviceTypeDriftWithViolations(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{{ID: 1, Name: "sw1", DeviceType: netbox.DeviceTypeRef{ID: 10, Model: "model"}}},
		InterfaceTemplates: []netbox.InterfaceTemplate{
			{Name: "eth0", DeviceType: &netbox.IDRef{ID: 10}, Type: choice("1000base-t"), Enabled: true},
			{Name: "eth1", DeviceType: &netbox.IDRef{ID: 10}, Type: choice("1000base-t"), Enabled: true},
		},
		Interfaces: []netbox.Iface{
			// missing eth1, eth0 has wrong type, plus an extra eth2.
			{ID: 100, Name: "eth0", Device: namedRef(1, "sw1"), Type: choice("10gbase-t"), Enabled: true},
			{ID: 102, Name: "eth2", Device: namedRef(1, "sw1"), Type: choice("1000base-t"), Enabled: true},
		},
	}
	got := DeviceTypeDrift(s)
	if len(got.Extra) != 1 {
		t.Fatalf("expected 1 drift record, got %v", got.Extra)
	}
	if got.Extra[0].Device != "sw1" {
		t.Errorf("device: %s", got.Extra[0].Device)
	}
	joined := strings.Join(got.Extra[0].Details, "\n")
	for _, want := range []string{"Interfaces missing", "Interfaces extra", "Interfaces mismatched"} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q in details:\n%s", want, joined)
		}
	}
	if len(got.Findings) != 1 {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestHoneypots(t *testing.T) {
	s := netbox.Snapshot{
		IPAddresses: []netbox.IPAddress{
			{Address: "10.0.0.5/24", Tags: []netbox.TagRef{{Slug: TagHoneypot}}},   // covers prefix 10.0.0.0/24
			{Address: "192.168.1.5/24", Tags: []netbox.TagRef{{Slug: TagHoneypot}}}, // not in any prefix
			{Address: "not-an-ip", Tags: []netbox.TagRef{{Slug: TagHoneypot}}},
		},
		Prefixes: []netbox.Prefix{
			{Prefix: "10.0.0.0/24", VLAN: &netbox.VLANRef{ID: 1, Name: "vlan1"}},
			{Prefix: "10.0.1.0/24", VLAN: &netbox.VLANRef{ID: 2, Name: "vlan2"}}, // uncovered
			{Prefix: "bad-prefix", VLAN: &netbox.VLANRef{ID: 3, Name: "vlan3"}},
		},
	}
	got := Honeypots(s)
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"vlan2",
		"is not inside any VLAN-backed prefix",
		"could not be parsed",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q:\n%s", want, joined)
		}
	}
}

func TestInterfaceVRFDisabled(t *testing.T) {
	s := netbox.Snapshot{Interfaces: []netbox.Iface{{Name: "eth0", Enabled: true, Mode: choicePtr("access")}}}
	indexSnapshot(&s)
	got := InterfaceVRF(s, InterfaceVRFRules{})
	if len(got.Findings) != 0 {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestInterfaceVRF(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{ID: 1, Name: "host", Role: namedRef(1, "Server"), Status: choice("active")},
			{ID: 2, Name: "wan", Role: namedRef(2, "WAN-Edge"), Status: choice("active")},
			{ID: 3, Name: "planned", Role: namedRef(1, "Server"), Status: choice(DeviceStatusPlanned)},
		},
		Interfaces: []netbox.Iface{
			// Missing VRF, has IPs/mode -> finding.
			{ID: 10, Name: "eth0", Device: namedRef(1, "host"), Enabled: true, Mode: choicePtr("access")},
			// WAN role -> exempt.
			{ID: 11, Name: "eth0", Device: namedRef(2, "wan"), Enabled: true, Mode: choicePtr("access")},
			// Planned device -> exempt.
			{ID: 12, Name: "eth0", Device: namedRef(3, "planned"), Enabled: true, Mode: choicePtr("access")},
			// Disabled -> exempt.
			{ID: 13, Name: "eth1", Device: namedRef(1, "host"), Enabled: false, Mode: choicePtr("access")},
			// Has VRF -> ok.
			{ID: 14, Name: "eth2", Device: namedRef(1, "host"), Enabled: true, VRF: &netbox.VRFRef{ID: 1}},
			// Empty / unconfigured -> exempt.
			{ID: 15, Name: "eth3", Device: namedRef(1, "host"), Enabled: true},
		},
	}
	indexSnapshot(&s)
	got := InterfaceVRF(s, InterfaceVRFRules{WANDeviceRoles: []string{"WAN-Edge"}, RequireOnInterfaces: true})
	if len(got.Findings) != 1 || !strings.Contains(got.Findings[0], "host eth0 is missing VRF") {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestIPVLANConsistency(t *testing.T) {
	s := netbox.Snapshot{
		Interfaces: []netbox.Iface{
			{ID: 10, Name: "eth0", Device: namedRef(1, "host"), Mode: choicePtr(VLANModeAccess), UntaggedVLAN: &netbox.VLANRef{ID: 99, Name: "wrong"}},
			{ID: 11, Name: "eth1", Device: namedRef(1, "host"), Mode: choicePtr(VLANModeAccess), UntaggedVLAN: &netbox.VLANRef{ID: 1, Name: "right"}},
		},
		IPAddresses: []netbox.IPAddress{
			{Address: "10.0.0.5/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 10},
			{Address: "10.0.0.6/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 11},
		},
		Prefixes: []netbox.Prefix{
			{Prefix: "10.0.0.0/24", VLAN: &netbox.VLANRef{ID: 1, Name: "right"}},
		},
	}
	indexSnapshot(&s)
	got := IPVLANConsistency(s)
	if len(got.Findings) != 1 || !strings.Contains(got.Findings[0], "best prefix VLAN is right") {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestMACConsistency(t *testing.T) {
	s := netbox.Snapshot{
		MACAddresses: []netbox.MACAddressRecord{
			{ID: 1, MACAddress: "aa:bb:cc:dd:ee:ff", AssignedObject: &netbox.AssignedObjectRef{Name: "eth0", Device: &netbox.NamedRef{Name: "h1"}}},
			{ID: 2, MACAddress: "AA:BB:CC:DD:EE:FF", AssignedObject: &netbox.AssignedObjectRef{Name: "eth0", Device: &netbox.NamedRef{Name: "h2"}}},
			{ID: 3, MACAddress: "11:22:33:44:55:66"},
			{ID: 4, MACAddress: ""},
		},
		Interfaces: []netbox.Iface{
			{Name: "eth0", Device: namedRef(1, "h1"), MACAddresses: []netbox.MACAddressRef{{MACAddress: "a"}, {MACAddress: "b"}}},
		},
	}
	got := MACConsistency(s)
	joined := strings.Join(got.Findings, "\n")
	if !strings.Contains(joined, "appears on multiple records") {
		t.Errorf("missing dup finding: %s", joined)
	}
	if !strings.Contains(joined, "no primary MAC") {
		t.Errorf("missing no-primary finding: %s", joined)
	}
}

func TestModuleConsistency(t *testing.T) {
	s := netbox.Snapshot{
		ModuleBays: []netbox.ModuleBay{
			{ID: 1, Name: "bay1", Device: namedRef(1, "sw1"), InstalledModule: &netbox.InstalledModuleRef{ID: 100}},
			{ID: 2, Name: "bay2", Device: namedRef(2, "sw2")},
			{ID: 3, Name: "bay3", Device: namedRef(1, "sw1"), InstalledModule: &netbox.InstalledModuleRef{ID: 999}}, // no module references it
		},
		Modules: []netbox.Module{
			{ID: 100, Device: namedRef(1, "sw1"), ModuleBay: &netbox.ModuleBayRef{ID: 1, Name: "bay1"}},
			{ID: 101, Device: namedRef(1, "sw1")},                                              // no module bay
			{ID: 102, Device: namedRef(1, "sw1"), ModuleBay: &netbox.ModuleBayRef{ID: 5555}}, // missing bay
			{ID: 103, Device: namedRef(3, "other"), ModuleBay: &netbox.ModuleBayRef{ID: 2}},   // wrong device
			{ID: 104, Device: namedRef(2, "sw2"), ModuleBay: &netbox.ModuleBayRef{ID: 2}},     // creates duplicate w/ 103
		},
	}
	indexSnapshot(&s)
	got := ModuleConsistency(s)
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"has no module bay",
		"references missing module bay",
		"is installed in bay",
		"has multiple installed modules",
		"points to installed module 999",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q:\n%s", want, joined)
		}
	}
}

func TestParentPlacement(t *testing.T) {
	site1 := &netbox.NamedRef{ID: 1, Name: "site1"}
	site2 := &netbox.NamedRef{ID: 2, Name: "site2"}
	rack1 := &netbox.NamedRef{ID: 1, Name: "r1"}
	rack2 := &netbox.NamedRef{ID: 2, Name: "r2"}
	loc1 := &netbox.NamedRef{ID: 1, Name: "l1"}
	loc2 := &netbox.NamedRef{ID: 2, Name: "l2"}

	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{ID: 1, Name: "parent", Site: site1, Rack: rack1, Location: loc1},
			{ID: 2, Name: "child-site-mismatch", ParentDevice: &netbox.NamedRef{ID: 1}, Site: site2, Rack: rack1, Location: loc1},
			{ID: 3, Name: "child-no-rack", ParentDevice: &netbox.NamedRef{ID: 1}, Site: site1, Location: loc1},
			{ID: 4, Name: "child-rack-mismatch", ParentDevice: &netbox.NamedRef{ID: 1}, Site: site1, Rack: rack2, Location: loc1},
			{ID: 5, Name: "child-loc-mismatch", ParentDevice: &netbox.NamedRef{ID: 1}, Site: site1, Rack: rack1, Location: loc2},
			{ID: 6, Name: "orphan", ParentDevice: &netbox.NamedRef{ID: 9999}},
			{ID: 7, Name: "parent2", Site: site1, Location: loc1},
			{ID: 8, Name: "child-extra-rack", ParentDevice: &netbox.NamedRef{ID: 7}, Site: site1, Rack: rack1, Location: loc1},
		},
	}
	indexSnapshot(&s)
	got := ParentPlacement(s)
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"site site2 differs",
		"is missing rack",
		"rack r2 differs",
		"location l2 differs",
		"references missing parent",
		"while parent parent2 has no rack",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q:\n%s", want, joined)
		}
	}
}

func TestPatchPanelContinuity(t *testing.T) {
	cable := &netbox.CableRef{ID: 1}
	s := netbox.Snapshot{
		RearPorts: []netbox.RearPort{
			{Name: "rp1", Device: namedRef(1, "pp"), Cable: cable, FrontPorts: []netbox.PortMap{{Position: 1}}}, // ok
			{Name: "rp2", Device: namedRef(1, "pp"), Cable: cable},                                              // bad
			{Name: "rp3", Device: namedRef(1, "pp")},                                                            // no cable -> ok
		},
		FrontPorts: []netbox.FrontPort{
			{Name: "fp1", Device: namedRef(1, "pp"), Cable: cable, RearPorts: []netbox.PortMap{{Position: 1}}}, // ok
			{Name: "fp2", Device: namedRef(1, "pp"), Cable: cable},                                             // bad
		},
	}
	got := PatchPanelContinuity(s)
	if len(got.Findings) != 2 {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestPlannedDevices(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{ID: 1, Name: "p", Status: choice(DeviceStatusPlanned)},
			{ID: 2, Name: "live", Status: choice("active")},
		},
		Interfaces: []netbox.Iface{
			{ID: 10, Name: "eth0", Device: namedRef(1, "p"), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 99}}},
			{ID: 11, Name: "eth1", Device: namedRef(1, "p"), MACAddress: "aa:bb:cc:dd:ee:ff"},
			{ID: 12, Name: "eth0", Device: namedRef(2, "live"), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 99}}},
		},
		IPAddresses: []netbox.IPAddress{
			{Address: "10.0.0.1/24", AssignedObjectType: netbox.ObjectTypeInterface, AssignedObjectID: 11},
		},
	}
	indexSnapshot(&s)
	got := PlannedDevices(s)
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"has a connected interface eth0",
		"has IPs assigned to interface eth1",
		"has MAC data on interface eth1",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q:\n%s", want, joined)
		}
	}
	if strings.Contains(joined, "live") {
		t.Errorf("non-planned device flagged: %s", joined)
	}
}

func TestPOEPowerDisabled(t *testing.T) {
	got := POEPower(netbox.Snapshot{}, POEPowerRules{})
	if len(got.Findings) != 0 {
		t.Fatalf("expected no findings: %v", got.Findings)
	}
}

func TestPOEPower(t *testing.T) {
	s := netbox.Snapshot{
		Interfaces: []netbox.Iface{
			// PD requiring AT, peer is PSE supplying BT4 -> ok.
			{ID: 1, Name: "pd1", Device: namedRef(1, "pd"), Enabled: true, POEMode: choicePtr(POEModePD), POEType: choicePtr(POETypeAT), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 2}}},
			{ID: 2, Name: "pse1", Device: namedRef(2, "pse"), Enabled: true, POEMode: choicePtr(POEModePSE), POEType: choicePtr(POETypeBT4)},
			// PD requiring BT4, peer supplies AF -> insufficient.
			{ID: 3, Name: "pd2", Device: namedRef(3, "pd2"), Enabled: true, POEMode: choicePtr(POEModePD), POEType: choicePtr(POETypeBT4), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 4}}},
			{ID: 4, Name: "pse2", Device: namedRef(4, "pse2"), Enabled: true, POEMode: choicePtr(POEModePSE), POEType: choicePtr(POETypeAF)},
			// PD with peer not in PSE mode.
			{ID: 5, Name: "pd3", Device: namedRef(5, "pd3"), Enabled: true, POEMode: choicePtr(POEModePD), POEType: choicePtr(POETypeAT), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 6}}},
			{ID: 6, Name: "peer3", Device: namedRef(6, "peer3"), Enabled: true},
			// PD whose peer is missing entirely from snapshot.
			{ID: 7, Name: "pd4", Device: namedRef(7, "pd4"), Enabled: true, POEMode: choicePtr(POEModePD), POEType: choicePtr(POETypeAT), ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 9999}}},
		},
	}
	indexSnapshot(&s)
	got := POEPower(s, POEPowerRules{CheckPoweredDeviceSupply: true, RequirePSEModeOnPeer: true, UnknownTypePolicy: POEUnknownTypeFail})
	joined := strings.Join(got.Findings, "\n")
	for _, want := range []string{
		"weaker than Powered Device requirement",
		"is not modeled as a PSE interface",
		"connected peer interface was not available",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q:\n%s", want, joined)
		}
	}
	if strings.Contains(joined, "pd1") {
		t.Errorf("ok pair flagged: %s", joined)
	}
}

func TestPrivateIPVRF(t *testing.T) {
	s := netbox.Snapshot{
		IPAddresses: []netbox.IPAddress{
			{Address: "10.0.0.1/24"},                              // private, no VRF
			{Address: "10.0.0.2/24", VRF: &netbox.VRFRef{ID: 1}}, // private, has VRF -> ok
			{Address: "8.8.8.8/32"},                               // public, no VRF
			{Address: "garbage"},                                  // unparseable -> skipped
		},
	}
	got := PrivateIPVRF(s, PrivateIPVRFRules{RequireOnPrivateIPs: true, RequireOnPublicIPs: true})
	if len(got.Findings) != 2 {
		t.Fatalf("findings: %v", got.Findings)
	}
	joined := strings.Join(got.Findings, "\n")
	if !strings.Contains(joined, "is private") || !strings.Contains(joined, "is public") {
		t.Errorf("findings missing expected: %s", joined)
	}
}

func TestRackPlacement(t *testing.T) {
	pos := func(v float64) *float64 { return &v }
	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{Name: "no-rack"}, // ignored
			{Name: "ok", Rack: &netbox.NamedRef{Name: "r1"}, Position: pos(1), Face: choicePtr("front")},
			{Name: "no-pos", Rack: &netbox.NamedRef{Name: "r1"}},
			{Name: "no-face", Rack: &netbox.NamedRef{Name: "r1"}, Position: pos(2)},
			{Name: "child", Rack: &netbox.NamedRef{Name: "r1"}, ParentDevice: &netbox.NamedRef{ID: 99}}, // exempt
			{Name: "tagged", Rack: &netbox.NamedRef{Name: "r1"}, Tags: []netbox.TagRef{{Slug: "exempt"}}},
		},
	}
	got := RackPlacement(s, RackPlacementRules{ExemptChildDevices: true, ExemptDeviceTags: []string{"exempt"}})
	if len(got.Findings) != 2 {
		t.Fatalf("findings: %v", got.Findings)
	}
	joined := strings.Join(got.Findings, "\n")
	if !strings.Contains(joined, "no-pos is in rack r1 without a rack position") {
		t.Errorf("missing no-pos: %s", joined)
	}
	if !strings.Contains(joined, "no-face is in rack r1 at position 2.0 without a face") {
		t.Errorf("missing no-face: %s", joined)
	}
}

func TestRequiredDeviceFields(t *testing.T) {
	s := netbox.Snapshot{Devices: []netbox.Device{
		{Name: "ok", Site: &netbox.NamedRef{ID: 1}, Role: namedRef(1, "Switch"), Status: choice("active")},
		{Name: "broken"},
	}}
	got := RequiredDeviceFields(s)
	if len(got.Findings) != 3 {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestSwitchLinkSymmetry(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{ID: 1, Name: "sw1", Role: namedRef(1, RoleSwitch)},
			{ID: 2, Name: "sw2", Role: namedRef(1, RoleSwitch)},
			{ID: 3, Name: "host", Role: namedRef(2, "Server")},
		},
		Interfaces: []netbox.Iface{
			{ID: 10, Name: "eth0", Device: namedRef(1, "sw1"), Mode: choicePtr("access"), UntaggedVLAN: &netbox.VLANRef{ID: 1}},
			{ID: 11, Name: "eth0", Device: namedRef(2, "sw2"), Mode: choicePtr("tagged"), UntaggedVLAN: &netbox.VLANRef{ID: 1}},
			{ID: 12, Name: "eth1", Device: namedRef(1, "sw1"), Mode: choicePtr("access"), UntaggedVLAN: &netbox.VLANRef{ID: 1}},
			{ID: 13, Name: "eth1", Device: namedRef(2, "sw2"), Mode: choicePtr("access"), UntaggedVLAN: &netbox.VLANRef{ID: 1}},
			{ID: 14, Name: "eth2", Device: namedRef(3, "host")},
			{ID: 15, Name: "eth2", Device: namedRef(1, "sw1"), Mode: choicePtr("access"), UntaggedVLAN: &netbox.VLANRef{ID: 9}},
		},
	}
	indexSnapshot(&s)
	s.Cables = []netbox.Cable{
		{ID: 1, ATerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 10}}, BTerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 11}}},
		{ID: 2, ATerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 12}}, BTerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 13}}},
		// host-to-switch: not switch-switch, ignored.
		{ID: 3, ATerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 14}}, BTerminations: []netbox.Termination{{ObjectType: netbox.ObjectTypeInterface, ObjectID: 15}}},
		// non-iface objects, ignored.
		{ID: 4, ATerminations: []netbox.Termination{{ObjectType: "dcim.console", ObjectID: 1}}, BTerminations: []netbox.Termination{{ObjectType: "dcim.console", ObjectID: 2}}},
	}
	got := SwitchLinkSymmetry(s)
	if len(got.Findings) != 1 || !strings.Contains(got.Findings[0], "asymmetric") || !strings.Contains(got.Findings[0], "cable #1") {
		t.Fatalf("findings: %v", got.Findings)
	}
}

func TestWirelessNormalization(t *testing.T) {
	s := netbox.Snapshot{
		Devices: []netbox.Device{
			{ID: 1, Name: "host"},
			{ID: 2, Name: "ap", Role: namedRef(1, RoleAccessPoint)},
			{ID: 3, Name: "planned", Status: choice(DeviceStatusPlanned)},
			{ID: 4, Name: "host-with-wired"},
		},
		Interfaces: []netbox.Iface{
			// host: wireless, missing all -> finding.
			{ID: 10, Name: "wlan0", Device: namedRef(1, "host"), Type: choice(WirelessTypePrefix + "ac"), Enabled: true},
			// AP: skipped (role).
			{ID: 11, Name: "wlan0", Device: namedRef(2, "ap"), Type: choice(WirelessTypePrefix + "ac"), Enabled: true},
			// planned: skipped.
			{ID: 12, Name: "wlan0", Device: namedRef(3, "planned"), Type: choice(WirelessTypePrefix + "ac"), Enabled: true},
			// host-with-wired: has a complete wired iface, so wireless suppressed.
			{ID: 13, Name: "eth0", Device: namedRef(4, "host-with-wired"), Type: choice("1000base-t"), Enabled: true, MACAddress: "aa:bb:cc:dd:ee:ff", ConnectedEndpoints: []netbox.ConnectedEndpoint{{ID: 1}}},
			{ID: 14, Name: "wlan0", Device: namedRef(4, "host-with-wired"), Type: choice(WirelessTypePrefix + "ac"), Enabled: true},
		},
	}
	indexSnapshot(&s)
	rules := WirelessNormalizationRules{
		SuppressIfConnectedWiredInterfaceIsComplete: true,
		RequireMode:         true,
		RequireUntaggedVLAN: true,
		RequirePrimaryMAC:   true,
	}
	got := WirelessNormalization(s, rules)
	if len(got.Findings) != 1 {
		t.Fatalf("findings: %v", got.Findings)
	}
	if !strings.Contains(got.Findings[0], "host wlan0") {
		t.Errorf("expected host wlan0 in finding, got: %s", got.Findings[0])
	}
	for _, want := range []string{"mode", "untagged_vlan", "primary_mac_address"} {
		if !strings.Contains(got.Findings[0], want) {
			t.Errorf("missing %q in: %s", want, got.Findings[0])
		}
	}
}

func TestPOESupplySufficient(t *testing.T) {
	cases := []struct {
		name     string
		supply   string
		required string
		policy   string
		ok       bool
	}{
		{"both AT", POETypeAT, POETypeAT, POEUnknownTypeFail, true},
		{"BT4 supplies AT", POETypeBT4, POETypeAT, POEUnknownTypeFail, true},
		{"AF cannot supply BT4", POETypeAF, POETypeBT4, POEUnknownTypeFail, false},
		{"missing required, fail policy", POETypeAT, "", POEUnknownTypeFail, false},
		{"missing required, ignore policy", POETypeAT, "", POEUnknownTypeIgnore, true},
		{"unrecognized required, fail", POETypeAT, "junk", POEUnknownTypeFail, false},
		{"unrecognized supply, fail", "junk", POETypeAT, POEUnknownTypeFail, false},
		{"missing supply, fail", "", POETypeAT, POEUnknownTypeFail, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ok, _ := poeSupplySufficient(tc.supply, tc.required, POEPowerRules{UnknownTypePolicy: tc.policy})
			if ok != tc.ok {
				t.Fatalf("got ok=%v want %v", ok, tc.ok)
			}
		})
	}
}

func TestFormatPortRanges(t *testing.T) {
	got := formatNames([]string{"Port 1", "Port 2", "Port 3", "Port 5", "eth0", "eth1"})
	want := []string{"Ports 1-3", "Port 5", "eth0", "eth1"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestExpandModuleTemplateName(t *testing.T) {
	if got := expandModuleTemplateName("Gi{module}/0/1", "module1"); got != "Gimodule1/0/1" {
		t.Errorf("got %q", got)
	}
	if got := expandModuleTemplateName("eth0", "module1"); got != "eth0" {
		t.Errorf("got %q", got)
	}
	if got := expandModuleTemplateName("Gi{module}/0", "Slot 0/1"); got != "Gi0/1/0" {
		t.Errorf("got %q", got)
	}
}
