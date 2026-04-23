package audit

import (
	"fmt"
	"net/netip"
	"regexp"
	"sort"
	"strings"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
)

type parsedPrefix struct {
	Prefix netip.Prefix
	VLAN   *netbox.VLANRef
	VRFID  int
}

type componentSpec struct {
	Type     string
	MgmtOnly *bool
	POEMode  string
	POEType  string
	Enabled  *bool
}

type componentDriftCheck struct {
	label                string
	expectedByDeviceType map[int]map[string]componentSpec
	expectedByModuleType map[int]map[string]componentSpec
	actualByDevice       map[int]map[string]componentSpec
	diffSpec             func(expected, actual componentSpec) []string
}

func (c componentDriftCheck) expectedForDevice(deviceTypeID int, modules []netbox.Module) map[string]componentSpec {
	out := cloneComponentMap(c.expectedByDeviceType[deviceTypeID])
	for _, mod := range modules {
		for name, spec := range c.expectedByModuleType[mod.ModuleType.ID] {
			expanded := expandModuleTemplateName(name, moduleBayName(mod))
			out[expanded] = spec
		}
	}
	return out
}

func newInterfaceDriftCheck(templates []netbox.InterfaceTemplate, instances []netbox.Iface) componentDriftCheck {
	byDT := map[int]map[string]componentSpec{}
	byMT := map[int]map[string]componentSpec{}
	for _, t := range templates {
		spec := componentSpec{
			Type:     t.Type.Value,
			MgmtOnly: boolPtr(t.MgmtOnly),
			POEMode:  choiceValue(t.POEMode),
			POEType:  choiceValue(t.POEType),
			Enabled:  boolPtr(t.Enabled),
		}
		if t.DeviceType != nil {
			ensureComponentMap(byDT, t.DeviceType.ID)[t.Name] = spec
		}
		if t.ModuleType != nil {
			ensureComponentMap(byMT, t.ModuleType.ID)[t.Name] = spec
		}
	}
	actual := map[int]map[string]componentSpec{}
	for _, it := range instances {
		ensureComponentMap(actual, it.Device.ID)[it.Name] = componentSpec{
			Type:     it.Type.Value,
			MgmtOnly: boolPtr(it.MgmtOnly),
			POEMode:  choiceValue(it.POEMode),
			POEType:  choiceValue(it.POEType),
			Enabled:  boolPtr(it.Enabled),
		}
	}
	return componentDriftCheck{
		label:                "Interfaces",
		expectedByDeviceType: byDT,
		expectedByModuleType: byMT,
		actualByDevice:       actual,
		diffSpec: func(expected, actual componentSpec) []string {
			var out []string
			if expected.Type != actual.Type {
				out = append(out, fmt.Sprintf("type: %s -> %s", actual.Type, expected.Type))
			}
			if derefBool(expected.MgmtOnly) != derefBool(actual.MgmtOnly) {
				out = append(out, fmt.Sprintf("mgmt_only: %t -> %t", derefBool(actual.MgmtOnly), derefBool(expected.MgmtOnly)))
			}
			if expected.POEMode != actual.POEMode {
				out = append(out, fmt.Sprintf("poe_mode: %s -> %s", blank(actual.POEMode), blank(expected.POEMode)))
			}
			if expected.POEType != actual.POEType {
				out = append(out, fmt.Sprintf("poe_type: %s -> %s", blank(actual.POEType), blank(expected.POEType)))
			}
			if derefBool(expected.Enabled) != derefBool(actual.Enabled) {
				out = append(out, fmt.Sprintf("enabled: %t -> %t", derefBool(actual.Enabled), derefBool(expected.Enabled)))
			}
			return out
		},
	}
}

func newTypedDriftCheck(
	label string,
	templates []netbox.TypedComponentTemplate,
	instances []netbox.TypedComponent,
) componentDriftCheck {
	byDT := map[int]map[string]componentSpec{}
	byMT := map[int]map[string]componentSpec{}
	for _, t := range templates {
		spec := componentSpec{Type: t.Type.Value}
		if t.DeviceType != nil {
			ensureComponentMap(byDT, t.DeviceType.ID)[t.Name] = spec
		}
		if t.ModuleType != nil {
			ensureComponentMap(byMT, t.ModuleType.ID)[t.Name] = spec
		}
	}
	actual := map[int]map[string]componentSpec{}
	for _, it := range instances {
		ensureComponentMap(actual, it.Device.ID)[it.Name] = componentSpec{Type: it.Type.Value}
	}
	return componentDriftCheck{
		label:                label,
		expectedByDeviceType: byDT,
		expectedByModuleType: byMT,
		actualByDevice:       actual,
		diffSpec: func(expected, actual componentSpec) []string {
			if expected.Type == actual.Type {
				return nil
			}
			return []string{fmt.Sprintf("type: %s -> %s", actual.Type, expected.Type)}
		},
	}
}

func newNamedDriftCheck(
	label string,
	templates []netbox.NamedComponentTemplate,
	instances []netbox.NamedComponent,
) componentDriftCheck {
	byDT := map[int]map[string]componentSpec{}
	byMT := map[int]map[string]componentSpec{}
	for _, t := range templates {
		if t.DeviceType != nil {
			ensureComponentMap(byDT, t.DeviceType.ID)[t.Name] = componentSpec{}
		}
		if t.ModuleType != nil {
			ensureComponentMap(byMT, t.ModuleType.ID)[t.Name] = componentSpec{}
		}
	}
	actual := map[int]map[string]componentSpec{}
	for _, it := range instances {
		ensureComponentMap(actual, it.Device.ID)[it.Name] = componentSpec{}
	}
	return componentDriftCheck{
		label:                label,
		expectedByDeviceType: byDT,
		expectedByModuleType: byMT,
		actualByDevice:       actual,
		diffSpec:             func(componentSpec, componentSpec) []string { return nil },
	}
}

func compareComponentMaps(
	label string,
	expected, actual map[string]componentSpec,
	diff func(expected, actual componentSpec) []string,
) []string {
	if expected == nil {
		expected = map[string]componentSpec{}
	}
	if actual == nil {
		actual = map[string]componentSpec{}
	}
	var details []string
	missing := diffNames(expected, actual)
	extra := diffNames(actual, expected)
	if len(missing) > 0 {
		details = append(details, fmt.Sprintf("%s missing: %s", label, strings.Join(formatNames(missing), ", ")))
	}
	if len(extra) > 0 {
		details = append(details, fmt.Sprintf("%s extra: %s", label, strings.Join(formatNames(extra), ", ")))
	}
	var mismatches []string
	for name, exp := range expected {
		act, ok := actual[name]
		if !ok {
			continue
		}
		diffs := diff(exp, act)
		if len(diffs) > 0 {
			mismatches = append(mismatches, fmt.Sprintf("%s %s", name, strings.Join(diffs, "; ")))
		}
	}
	sort.Strings(mismatches)
	if len(mismatches) > 0 {
		details = append(details, fmt.Sprintf("%s mismatched: %s", label, strings.Join(mismatches, "; ")))
	}
	return details
}

func poeSupplySufficient(supplyType, requiredType string, rules POEPowerRules) (bool, string) {
	var unknownTypesAllowed = rules.UnknownTypePolicy == POEUnknownTypeIgnore

	if requiredType == "" {
		if unknownTypesAllowed {
			return true, ""
		}
		return false, "Powered Device interface is missing poe_type"
	}

	// If we can't determine the rank of the required type, we can't determine if it's sufficient.
	requiredRank, ok := poeTypeRank(requiredType)
	if !ok {
		if unknownTypesAllowed {
			return true, ""
		}
		return false, "Powered Device interface has an unrecognized poe_type"
	}

	if supplyType == "" {
		if unknownTypesAllowed {
			return true, ""
		}
		return false, "Power Supplying Equipment interface is missing poe_type"
	}

	// If we can't determine the rank of the supply type, we can't determine if it's sufficient.
	supplyRank, ok := poeTypeRank(supplyType)
	if !ok {
		if unknownTypesAllowed {
			return true, ""
		}
		return false, "Power Supplying Equipment interface has an unrecognized poe_type"
	}

	//TODO: I'd like to add a tag to the model to indicate that a device is being powered directly
	// instead of through PoE. This logic will need to change. Or maybe the logic that decides to call
	// this could check for that tag and skip this check.
	if supplyRank >= requiredRank {
		return true, ""
	}
	return false, "Power Supplying Equipment PoE type is weaker than Powered Device requirement"
}

func poeTypeRank(v string) (int, bool) {
	switch v {
	case POETypeAF:
		return 1, true
	case POETypeAT:
		return 2, true
	case POETypeBT3:
		return 3, true
	case POETypeBT4:
		return 4, true
	default:
		return 0, false
	}
}

func isWANInterface(
	it netbox.Iface,
	dev netbox.Device,
	devices map[int]netbox.Device,
	rules InterfaceVRFRules,
) bool {
	if rules.IsWANRole(dev.Role.Name) {
		return true
	}
	for _, ep := range it.ConnectedEndpoints {
		if ep.Device == nil {
			continue
		}
		peer, ok := devices[ep.Device.ID]
		if ok && rules.IsWANRole(peer.Role.Name) {
			return true
		}
	}
	return false
}

func sameSwitchPortConfig(a, b netbox.Iface) bool {
	if choiceValue(a.Mode) != choiceValue(b.Mode) || vlanID(a.UntaggedVLAN) != vlanID(b.UntaggedVLAN) {
		return false
	}

	at := taggedVLANIDs(a.TaggedVLANs)
	bt := taggedVLANIDs(b.TaggedVLANs)
	if len(at) != len(bt) {
		return false
	}
	for i := range at {
		if at[i] != bt[i] {
			return false
		}
	}
	return true
}

func parsePrefixes(prefixes []netbox.Prefix) []parsedPrefix {
	out := make([]parsedPrefix, 0, len(prefixes))
	for _, p := range prefixes {
		net, err := netip.ParsePrefix(p.Prefix)
		if err != nil {
			continue
		}
		out = append(out, parsedPrefix{Prefix: net, VLAN: p.VLAN, VRFID: vrfID(p.VRF)})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Prefix.Bits() > out[j].Prefix.Bits() })
	return out
}

func bestPrefixMatch(prefixes []parsedPrefix, addr netip.Addr, vrf int) *parsedPrefix {
	for _, p := range prefixes {
		if p.VRFID != vrf {
			continue
		}
		if p.Prefix.Contains(addr) {
			p := p
			return &p
		}
	}
	return nil
}

func taggedRanges(ranges []netbox.IPRange, slug string) []netbox.IPRange {
	var out []netbox.IPRange
	for _, r := range ranges {
		if hasTag(r.Tags, slug) {
			out = append(out, r)
		}
	}
	return out
}

func overlappingRanges(a, b []netbox.IPRange) []string {
	var findings []string
	for _, ra := range a {
		astart, aok := bareAddr(ra.StartAddress)
		aend, aendOK := bareAddr(ra.EndAddress)
		if !aok || !aendOK {
			continue
		}
		for _, rb := range b {
			if vrfID(ra.VRF) != vrfID(rb.VRF) {
				continue
			}
			bstart, bok := bareAddr(rb.StartAddress)
			bend, bendOK := bareAddr(rb.EndAddress)
			if !bok || !bendOK || astart.BitLen() != bstart.BitLen() {
				continue
			}
			if rangesOverlap(astart, aend, bstart, bend) {
				findings = append(
					findings,
					fmt.Sprintf(
						"Range overlap between %s-%s and %s-%s",
						ra.StartAddress, ra.EndAddress, rb.StartAddress, rb.EndAddress,
					),
				)
			}
		}
	}
	sort.Strings(findings)
	return findings
}

func rangesOverlap(aStart, aEnd, bStart, bEnd netip.Addr) bool {
	return aStart.Compare(bEnd) <= 0 && bStart.Compare(aEnd) <= 0
}

func ipInRanges(ip netbox.IPAddress, ranges []netbox.IPRange) bool {
	addr, ok := bareAddr(ip.Address)
	if !ok {
		return false
	}
	for _, r := range ranges {
		if vrfID(ip.VRF) != vrfID(r.VRF) {
			continue
		}
		start, ok1 := bareAddr(r.StartAddress)
		end, ok2 := bareAddr(r.EndAddress)
		if !ok1 || !ok2 || start.BitLen() != addr.BitLen() {
			continue
		}
		if start.Compare(addr) <= 0 && addr.Compare(end) <= 0 {
			return true
		}
	}
	return false
}

func resolveMAC(it netbox.Iface) (mac string, ok bool, multi bool) {
	if it.PrimaryMACAddress != nil && it.PrimaryMACAddress.MACAddress != "" {
		return it.PrimaryMACAddress.MACAddress, true, len(it.MACAddresses) > 1
	}
	if it.MACAddress != "" {
		return it.MACAddress, true, len(it.MACAddresses) > 1
	}
	if len(it.MACAddresses) == 1 && it.MACAddresses[0].MACAddress != "" {
		return it.MACAddresses[0].MACAddress, true, false
	}
	if len(it.MACAddresses) > 1 {
		return "", false, true
	}
	return "", false, false
}

func interfaceHasMAC(it netbox.Iface) bool {
	_, ok, _ := resolveMAC(it)
	return ok
}

func wiredInterfaceComplete(it netbox.Iface, ips []netbox.IPAddress) bool {
	return len(it.ConnectedEndpoints) > 0 &&
		(it.VRF != nil ||
			it.Mode != nil ||
			it.UntaggedVLAN != nil ||
			len(ips) > 0 ||
			interfaceHasMAC(it))
}

func isWirelessType(t string) bool {
	return strings.HasPrefix(t, WirelessTypePrefix)
}

func boolPtr(v bool) *bool { return &v }

func derefBool(v *bool) bool {
	if v == nil {
		return false
	}
	return *v
}

func choiceValue(v *netbox.Choice) string {
	if v == nil {
		return ""
	}
	return v.Value
}

func blank(v string) string {
	if v == "" {
		return "<blank>"
	}
	return v
}

func bareAddr(cidr string) (netip.Addr, bool) {
	if prefix, err := netip.ParsePrefix(cidr); err == nil {
		return prefix.Addr(), true
	}
	if addr, err := netip.ParseAddr(cidr); err == nil {
		return addr, true
	}
	return netip.Addr{}, false
}

func vrfID(vrf *netbox.VRFRef) int {
	if vrf == nil {
		return 0
	}
	return vrf.ID
}

func hasTag(tags []netbox.TagRef, slug string) bool {
	for _, tag := range tags {
		if tag.Slug == slug {
			return true
		}
	}
	return false
}

func normalizeMAC(v string) string {
	return strings.ToUpper(strings.TrimSpace(v))
}

func describeAssignedObject(obj *netbox.AssignedObjectRef) string {
	if obj == nil {
		return "<unassigned>"
	}
	if obj.Device != nil {
		return fmt.Sprintf("%s %s", obj.Device.Name, obj.Name)
	}
	return obj.Name
}

func ensureComponentMap(m map[int]map[string]componentSpec, id int) map[string]componentSpec {
	if _, ok := m[id]; !ok {
		m[id] = map[string]componentSpec{}
	}
	return m[id]
}

func cloneComponentMap(src map[string]componentSpec) map[string]componentSpec {
	out := map[string]componentSpec{}
	for name, spec := range src {
		out[name] = spec
	}
	return out
}

func diffNames(a, b map[string]componentSpec) []string {
	var names []string
	for name := range a {
		if _, ok := b[name]; !ok {
			names = append(names, name)
		}
	}
	sort.Strings(names)
	return names
}

func formatNames(names []string) []string {
	portRanges, others := groupPortNames(names)
	out := formatPortRanges(portRanges)
	out = append(out, others...)
	return out
}

func groupPortNames(names []string) ([][2]int, []string) {
	var nums []int
	var others []string
	for _, name := range names {
		if n, ok := parsePortNumber(name); ok {
			nums = append(nums, n)
		} else {
			others = append(others, name)
		}
	}
	sort.Ints(nums)
	sort.Strings(others)
	var ranges [][2]int
	if len(nums) > 0 {
		start, prev := nums[0], nums[0]
		for _, n := range nums[1:] {
			if n == prev+1 {
				prev = n
				continue
			}
			ranges = append(ranges, [2]int{start, prev})
			start, prev = n, n
		}
		ranges = append(ranges, [2]int{start, prev})
	}
	return ranges, others
}

func formatPortRanges(ranges [][2]int) []string {
	out := make([]string, 0, len(ranges))
	for _, r := range ranges {
		if r[0] == r[1] {
			out = append(out, fmt.Sprintf("Port %d", r[0]))
		} else {
			out = append(out, fmt.Sprintf("Ports %d-%d", r[0], r[1]))
		}
	}
	return out
}

var portPattern = regexp.MustCompile(`^Port (\d+)$`)
var trailingTokenPattern = regexp.MustCompile(`([A-Za-z0-9/_-]+)$`)

func parsePortNumber(name string) (int, bool) {
	m := portPattern.FindStringSubmatch(name)
	if len(m) != 2 {
		return 0, false
	}
	var n int
	_, err := fmt.Sscanf(m[1], "%d", &n)
	return n, err == nil
}

func moduleBayName(mod netbox.Module) string {
	if mod.ModuleBay == nil {
		return "module"
	}
	return mod.ModuleBay.Name
}

func expandModuleTemplateName(templateName, bayName string) string {
	if !strings.Contains(templateName, "{module}") {
		return templateName
	}
	token := bayName
	if match := trailingTokenPattern.FindStringSubmatch(bayName); len(match) == 2 {
		token = match[1]
	}
	return strings.ReplaceAll(templateName, "{module}", token)
}

func frontTemplatesToTyped(in []netbox.FrontPortTemplate) []netbox.TypedComponentTemplate {
	out := make([]netbox.TypedComponentTemplate, 0, len(in))
	for _, fp := range in {
		out = append(out, netbox.TypedComponentTemplate(fp))
	}
	return out
}

func rearTemplatesToTyped(in []netbox.RearPortTemplate) []netbox.TypedComponentTemplate {
	out := make([]netbox.TypedComponentTemplate, 0, len(in))
	for _, rp := range in {
		out = append(out, netbox.TypedComponentTemplate(rp))
	}
	return out
}

func frontPortsToTyped(in []netbox.FrontPort) []netbox.TypedComponent {
	out := make([]netbox.TypedComponent, 0, len(in))
	for _, fp := range in {
		out = append(out, netbox.TypedComponent{
			ID:     fp.ID,
			Name:   fp.Name,
			Device: fp.Device,
			Module: fp.Module,
			Type:   fp.Type,
		})
	}
	return out
}

func rearPortsToTyped(in []netbox.RearPort) []netbox.TypedComponent {
	out := make([]netbox.TypedComponent, 0, len(in))
	for _, rp := range in {
		out = append(out, netbox.TypedComponent{
			ID:     rp.ID,
			Name:   rp.Name,
			Device: rp.Device,
			Module: rp.Module,
			Type:   rp.Type,
		})
	}
	return out
}

func moduleBaysToNamed(in []netbox.ModuleBay) []netbox.NamedComponent {
	out := make([]netbox.NamedComponent, 0, len(in))
	for _, mb := range in {
		out = append(out, netbox.NamedComponent{
			ID:     mb.ID,
			Name:   mb.Name,
			Device: mb.Device,
			Module: mb.Module,
		})
	}
	return out
}

func vlanID(v *netbox.VLANRef) int {
	if v == nil {
		return 0
	}
	return v.ID
}

func taggedVLANIDs(vlans []netbox.VLANRef) []int {
	out := make([]int, 0, len(vlans))
	for _, vlan := range vlans {
		out = append(out, vlan.ID)
	}
	sort.Ints(out)
	return out
}
