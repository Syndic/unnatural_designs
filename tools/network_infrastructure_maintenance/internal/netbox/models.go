package netbox

import "time"

type Choice struct {
	Value string `json:"value"`
	Label string `json:"label"`
}

type IDRef struct {
	ID int `json:"id"`
}

type NamedRef struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

type DeviceTypeRef struct {
	ID    int    `json:"id"`
	Model string `json:"model"`
}

type ModuleTypeRef struct {
	ID    int    `json:"id"`
	Model string `json:"model"`
}

type VLANRef struct {
	ID   int    `json:"id"`
	VID  int    `json:"vid"`
	Name string `json:"name"`
}

type VRFRef struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

type MACAddressRef struct {
	ID         int    `json:"id"`
	MACAddress string `json:"mac_address"`
}

type ModuleBayRef struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

type ModuleRef struct {
	ID        int           `json:"id"`
	ModuleBay *ModuleBayRef `json:"module_bay"`
}

type Device struct {
	ID           int           `json:"id"`
	Name         string        `json:"name"`
	DeviceType   DeviceTypeRef `json:"device_type"`
	Role         NamedRef      `json:"role"`
	Status       Choice        `json:"status"`
	Site         *NamedRef     `json:"site"`
	Location     *NamedRef     `json:"location"`
	Rack         *NamedRef     `json:"rack"`
	Position     *float64      `json:"position"`
	Face         *Choice       `json:"face"`
	ParentDevice *NamedRef     `json:"parent_device"`
	Description  string        `json:"description"`
	Tags         []TagRef      `json:"tags"`
}

type ConnectedEndpoint struct {
	ID          int       `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	Device      *NamedRef `json:"device"`
}

type Iface struct {
	ID                 int                 `json:"id"`
	Name               string              `json:"name"`
	Device             NamedRef            `json:"device"`
	Module             *ModuleRef          `json:"module"`
	Type               Choice              `json:"type"`
	MgmtOnly           bool                `json:"mgmt_only"`
	POEMode            *Choice             `json:"poe_mode"`
	POEType            *Choice             `json:"poe_type"`
	Enabled            bool                `json:"enabled"`
	ConnectedEndpoints []ConnectedEndpoint `json:"connected_endpoints"`
	Mode               *Choice             `json:"mode"`
	UntaggedVLAN       *VLANRef            `json:"untagged_vlan"`
	TaggedVLANs        []VLANRef           `json:"tagged_vlans"`
	VRF                *VRFRef             `json:"vrf"`
	MACAddress         string              `json:"mac_address"`
	PrimaryMACAddress  *MACAddressRef      `json:"primary_mac_address"`
	MACAddresses       []MACAddressRef     `json:"mac_addresses"`
	Description        string              `json:"description"`
}

type InterfaceTemplate struct {
	ID         int     `json:"id"`
	DeviceType *IDRef  `json:"device_type"`
	ModuleType *IDRef  `json:"module_type"`
	Name       string  `json:"name"`
	Type       Choice  `json:"type"`
	Enabled    bool    `json:"enabled"`
	MgmtOnly   bool    `json:"mgmt_only"`
	POEMode    *Choice `json:"poe_mode"`
	POEType    *Choice `json:"poe_type"`
}

type TypedComponent struct {
	ID     int        `json:"id"`
	Name   string     `json:"name"`
	Device NamedRef   `json:"device"`
	Module *ModuleRef `json:"module"`
	Type   Choice     `json:"type"`
}

type TypedComponentTemplate struct {
	ID         int    `json:"id"`
	DeviceType *IDRef `json:"device_type"`
	ModuleType *IDRef `json:"module_type"`
	Name       string `json:"name"`
	Type       Choice `json:"type"`
}

type NamedComponent struct {
	ID     int        `json:"id"`
	Name   string     `json:"name"`
	Device NamedRef   `json:"device"`
	Module *ModuleRef `json:"module"`
}

type NamedComponentTemplate struct {
	ID         int    `json:"id"`
	DeviceType *IDRef `json:"device_type"`
	ModuleType *IDRef `json:"module_type"`
	Name       string `json:"name"`
}

type PortMap struct {
	Position  int `json:"position"`
	FrontPort int `json:"front_port"`
	RearPort  int `json:"rear_port"`
}

type CableRef struct {
	ID int `json:"id"`
}

type FrontPort struct {
	ID        int        `json:"id"`
	Name      string     `json:"name"`
	Device    NamedRef   `json:"device"`
	Module    *ModuleRef `json:"module"`
	Type      Choice     `json:"type"`
	Cable     *CableRef  `json:"cable"`
	RearPorts []PortMap  `json:"rear_ports"`
}

type RearPort struct {
	ID         int        `json:"id"`
	Name       string     `json:"name"`
	Device     NamedRef   `json:"device"`
	Module     *ModuleRef `json:"module"`
	Type       Choice     `json:"type"`
	Cable      *CableRef  `json:"cable"`
	FrontPorts []PortMap  `json:"front_ports"`
}

type FrontPortTemplate struct {
	ID         int    `json:"id"`
	DeviceType *IDRef `json:"device_type"`
	ModuleType *IDRef `json:"module_type"`
	Name       string `json:"name"`
	Type       Choice `json:"type"`
}

type RearPortTemplate struct {
	ID         int    `json:"id"`
	DeviceType *IDRef `json:"device_type"`
	ModuleType *IDRef `json:"module_type"`
	Name       string `json:"name"`
	Type       Choice `json:"type"`
}

type InstalledModuleRef struct {
	ID int `json:"id"`
}

type ModuleBay struct {
	ID              int                 `json:"id"`
	Name            string              `json:"name"`
	Device          NamedRef            `json:"device"`
	Module          *ModuleRef          `json:"module"`
	InstalledModule *InstalledModuleRef `json:"installed_module"`
}

type Module struct {
	ID         int           `json:"id"`
	Device     NamedRef      `json:"device"`
	ModuleBay  *ModuleBayRef `json:"module_bay"`
	ModuleType ModuleTypeRef `json:"module_type"`
}

type TagRef struct {
	Name string `json:"name"`
	Slug string `json:"slug"`
}

type AssignedObjectRef struct {
	ID     int       `json:"id"`
	Name   string    `json:"name"`
	Device *NamedRef `json:"device"`
}

type IPAddress struct {
	ID                 int                `json:"id"`
	Address            string             `json:"address"`
	VRF                *VRFRef            `json:"vrf"`
	Status             Choice             `json:"status"`
	DNSName            string             `json:"dns_name"`
	AssignedObjectType string             `json:"assigned_object_type"`
	AssignedObjectID   int                `json:"assigned_object_id"`
	AssignedObject     *AssignedObjectRef `json:"assigned_object"`
	Description        string             `json:"description"`
	Tags               []TagRef           `json:"tags"`
}

type IPRange struct {
	ID           int      `json:"id"`
	StartAddress string   `json:"start_address"`
	EndAddress   string   `json:"end_address"`
	VRF          *VRFRef  `json:"vrf"`
	Tags         []TagRef `json:"tags"`
}

type Prefix struct {
	ID     int      `json:"id"`
	Prefix string   `json:"prefix"`
	VRF    *VRFRef  `json:"vrf"`
	VLAN   *VLANRef `json:"vlan"`
}

type TerminationObject struct {
	ID     int       `json:"id"`
	Name   string    `json:"name"`
	Device *NamedRef `json:"device"`
}

type Termination struct {
	ObjectType string             `json:"object_type"`
	ObjectID   int                `json:"object_id"`
	Object     *TerminationObject `json:"object"`
}

type Cable struct {
	ID            int           `json:"id"`
	Type          string        `json:"type"`
	Status        Choice        `json:"status"`
	ATerminations []Termination `json:"a_terminations"`
	BTerminations []Termination `json:"b_terminations"`
}

type MACAddressRecord struct {
	ID                 int                `json:"id"`
	MACAddress         string             `json:"mac_address"`
	AssignedObjectType string             `json:"assigned_object_type"`
	AssignedObjectID   int                `json:"assigned_object_id"`
	AssignedObject     *AssignedObjectRef `json:"assigned_object"`
}

type Snapshot struct {
	LatestChange               ObjectChange
	SnapshotAttempts           int
	LoadStats                  SnapshotLoadStats
	Devices                    []Device
	Interfaces                 []Iface
	InterfaceTemplates         []InterfaceTemplate
	ConsolePorts               []TypedComponent
	ConsolePortTemplates       []TypedComponentTemplate
	ConsoleServerPorts         []TypedComponent
	ConsoleServerPortTemplates []TypedComponentTemplate
	PowerPorts                 []TypedComponent
	PowerPortTemplates         []TypedComponentTemplate
	PowerOutlets               []TypedComponent
	PowerOutletTemplates       []TypedComponentTemplate
	FrontPorts                 []FrontPort
	FrontPortTemplates         []FrontPortTemplate
	RearPorts                  []RearPort
	RearPortTemplates          []RearPortTemplate
	DeviceBays                 []NamedComponent
	DeviceBayTemplates         []NamedComponentTemplate
	ModuleBays                 []ModuleBay
	ModuleBayTemplates         []NamedComponentTemplate
	Modules                    []Module
	IPAddresses                []IPAddress
	IPRanges                   []IPRange
	Prefixes                   []Prefix
	Cables                     []Cable
	MACAddresses               []MACAddressRecord

	// Pre-computed indexes — built once via BuildIndexes after fetch, used by
	// all parallel checks. Kept unexported so callers go through the accessor
	// methods (DeviceByID, InterfacesForDevice, ...); the slices above are the
	// single canonical iteration form.
	devicesByID        map[int]Device
	interfacesByID     map[int]Iface
	interfacesByDevice map[int][]Iface
	ipsByInterface     map[int][]IPAddress
	moduleBaysByID     map[int]ModuleBay
	modulesByDevice    map[int][]Module
	modulesByBay       map[int][]Module
}

// DeviceByID returns the device with the given ID and whether it was present
// in the snapshot.
func (s *Snapshot) DeviceByID(id int) (Device, bool) {
	d, ok := s.devicesByID[id]
	return d, ok
}

// InterfaceByID returns the interface with the given ID and whether it was
// present in the snapshot.
func (s *Snapshot) InterfaceByID(id int) (Iface, bool) {
	it, ok := s.interfacesByID[id]
	return it, ok
}

// InterfacesForDevice returns the interfaces belonging to the given device, in
// snapshot order. Returns nil for unknown device IDs.
func (s *Snapshot) InterfacesForDevice(deviceID int) []Iface {
	return s.interfacesByDevice[deviceID]
}

// IPsForInterface returns the IP addresses assigned to the given interface, in
// snapshot order. Returns nil for unknown interface IDs.
func (s *Snapshot) IPsForInterface(ifaceID int) []IPAddress {
	return s.ipsByInterface[ifaceID]
}

// ModuleBayByID returns the module bay with the given ID and whether it was
// present in the snapshot.
func (s *Snapshot) ModuleBayByID(id int) (ModuleBay, bool) {
	mb, ok := s.moduleBaysByID[id]
	return mb, ok
}

// ModulesForDevice returns the modules installed on the given device.
func (s *Snapshot) ModulesForDevice(deviceID int) []Module {
	return s.modulesByDevice[deviceID]
}

// ModulesForBay returns the modules installed in the given module bay. Only
// modules whose bay is present in the snapshot are indexed; modules referencing
// a missing bay are surfaced separately by audits, not silently grouped here.
func (s *Snapshot) ModulesForBay(bayID int) []Module {
	return s.modulesByBay[bayID]
}

type SnapshotLoadStats struct {
	Duration     time.Duration
	RequestCount int
	Fetches      []FetchTiming
}

type FetchTiming struct {
	Name     string
	Requests int
	Duration time.Duration
	Pages    int
	Items    int
}
