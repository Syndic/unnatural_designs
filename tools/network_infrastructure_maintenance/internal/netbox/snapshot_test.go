package netbox

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// recordingObserver captures the per-task progress callbacks created by
// SnapshotTaskStart and records every (name, items, total, requests) tick.
type recordingObserver struct {
	mu        sync.Mutex
	starts    []string
	completes []string
	ticks     map[string]int // task name -> number of progress callbacks fired
}

func newRecordingObserver() *recordingObserver {
	return &recordingObserver{ticks: make(map[string]int)}
}

func (o *recordingObserver) SnapshotAttemptStart(int, int, int) {}

func (o *recordingObserver) SnapshotTaskStart(name string) TaskProgress {
	o.mu.Lock()
	o.starts = append(o.starts, name)
	o.mu.Unlock()
	return func(items, total, reqs int) {
		o.mu.Lock()
		o.ticks[name]++
		o.mu.Unlock()
	}
}

func (o *recordingObserver) SnapshotTaskComplete(_ int, _ int, stats FetchTiming, _ int) {
	o.mu.Lock()
	o.completes = append(o.completes, stats.Name)
	o.mu.Unlock()
}

func (o *recordingObserver) SnapshotLoadError(int, int, error) {}
func (o *recordingObserver) SnapshotLoadRetryDelay(time.Duration) {}

// TestSnapshotTaskProgressPlumbing verifies that the per-task progress
// callback returned from SnapshotTaskStart is plumbed all the way through
// snapshotTask.run -> fetchAll -> FetchAllWithProgress and actually fires
// for every task during a snapshot load.
func TestSnapshotTaskProgressPlumbing(t *testing.T) {
	// Empty NetBox: every collection returns zero items in a single page.
	// One page still triggers exactly one progress callback per task.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	defer srv.Close()

	obs := newRecordingObserver()
	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}

	// LatestChange is called twice (start/end) by LoadConsistentSnapshot;
	// since the same handler answers everything with count:0, both calls
	// return an empty ObjectChange so the snapshot is considered coherent.
	snap, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 1 {
		t.Errorf("SnapshotAttempts=%d, want 1", snap.SnapshotAttempts)
	}

	wantTasks := SnapshotTaskCount()
	if got := len(obs.starts); got != wantTasks {
		t.Errorf("SnapshotTaskStart fired %d times, want %d", got, wantTasks)
	}
	if got := len(obs.completes); got != wantTasks {
		t.Errorf("SnapshotTaskComplete fired %d times, want %d", got, wantTasks)
	}
	if got := len(obs.ticks); got != wantTasks {
		t.Errorf("progress callback fired for %d distinct tasks, want %d", got, wantTasks)
	}
	for name, count := range obs.ticks {
		if count != 1 {
			t.Errorf("task %q: progress fired %d times, want 1 (one page)", name, count)
		}
	}
}

// TestSnapshotTaskProgressNilObserver confirms loadSnapshot tolerates a nil
// observer (passes nil progress through cleanly).
func TestSnapshotTaskProgressNilObserver(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	defer srv.Close()

	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	if _, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, nil); err != nil {
		t.Fatalf("LoadConsistentSnapshot with nil observer: %v", err)
	}
}
