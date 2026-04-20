package netbox

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestFetchAllWithProgress_PaginatesAndReportsCounts(t *testing.T) {
	// Two-page server: page 1 returns 2 of 5 items with a Next URL pointing
	// at this same handler with ?page=2; page 2 returns the remaining 3 with
	// no Next.
	mux := http.NewServeMux()
	var srv *httptest.Server
	mux.HandleFunc("/api/things/", func(w http.ResponseWriter, r *http.Request) {
		page := r.URL.Query().Get("page")
		if page == "2" {
			_, _ = w.Write([]byte(`{"count":5,"next":null,"results":[{"v":3},{"v":4},{"v":5}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"count":5,"next":"` + srv.URL + `/api/things/?page=2","results":[{"v":1},{"v":2}]}`))
	})
	srv = httptest.NewServer(mux)
	defer srv.Close()

	type item struct {
		V int `json:"v"`
	}
	type tick struct{ items, total, reqs int }
	var ticks []tick
	progress := func(items, total, reqs int) {
		ticks = append(ticks, tick{items, total, reqs})
	}

	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	out, requests, pages, err := FetchAllWithProgress[item](context.Background(), client, "/api/things/", progress)
	if err != nil {
		t.Fatalf("FetchAllWithProgress: %v", err)
	}
	if len(out) != 5 {
		t.Errorf("len(out)=%d, want 5", len(out))
	}
	if requests != 2 {
		t.Errorf("requests=%d, want 2", requests)
	}
	if pages != 2 {
		t.Errorf("pages=%d, want 2", pages)
	}
	if got, want := len(ticks), 2; got != want {
		t.Fatalf("got %d progress ticks, want %d (ticks=%+v)", got, want, ticks)
	}
	if ticks[0] != (tick{items: 2, total: 5, reqs: 1}) {
		t.Errorf("tick[0]=%+v, want {items:2, total:5, reqs:1}", ticks[0])
	}
	if ticks[1] != (tick{items: 5, total: 5, reqs: 2}) {
		t.Errorf("tick[1]=%+v, want {items:5, total:5, reqs:2}", ticks[1])
	}
}

func TestFetchAllWithProgress_NilCallbackBehaviorMatchesFetchAll(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/empty/", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	type item struct{}
	out, requests, pages, err := FetchAll[item](context.Background(), client, "/api/empty/")
	if err != nil {
		t.Fatalf("FetchAll: %v", err)
	}
	if len(out) != 0 || requests != 1 || pages != 1 {
		t.Errorf("FetchAll empty: len=%d req=%d pages=%d, want 0/1/1", len(out), requests, pages)
	}
}
