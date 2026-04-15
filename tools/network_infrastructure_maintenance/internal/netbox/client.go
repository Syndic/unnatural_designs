package netbox

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const DefaultPageSize = 1000

const (
	protocolHTTP        = "http://"
	protocolHTTPS       = "https://"
	queryLimit          = "limit"
	headerAuthorization = "Authorization"
	headerAccept        = "Accept"
	headerCacheControl  = "Cache-Control"
	headerPragma        = "Pragma"
	headerValueNoCache  = "no-cache"
	acceptApplicationJS = "application/json"
	tokenPrefix         = "Token "
)

type Client struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

type ObjectChange struct {
	ID      int    `json:"id"`
	Display string `json:"display"`
}

type page[T any] struct {
	Count   int     `json:"count"`
	Next    *string `json:"next"`
	Results []T     `json:"results"`
}

func (c *Client) LatestChange(ctx context.Context) (ObjectChange, error) {
	urlStr, err := c.ResolveURL("/api/core/object-changes/?limit=1")
	if err != nil {
		return ObjectChange{}, err
	}
	body, err := c.DoRequest(ctx, urlStr)
	if err != nil {
		return ObjectChange{}, err
	}
	var p page[ObjectChange]
	if err := json.Unmarshal(body, &p); err != nil {
		return ObjectChange{}, err
	}
	if len(p.Results) == 0 {
		return ObjectChange{}, nil
	}
	return p.Results[0], nil
}

func FetchAll[T any](ctx context.Context, client *Client, path string) ([]T, int, int, error) {
	urlStr, err := client.ResolveURL(path)
	if err != nil {
		return nil, 0, 0, err
	}
	var out []T
	requests := 0
	pagesSeen := 0
	for urlStr != "" {
		body, err := client.DoRequest(ctx, urlStr)
		if err != nil {
			return nil, requests, pagesSeen, err
		}
		requests++
		pagesSeen++
		var p page[T]
		if err := json.Unmarshal(body, &p); err != nil {
			return nil, requests, pagesSeen, err
		}
		out = append(out, p.Results...)
		if p.Next != nil {
			urlStr = *p.Next
		} else {
			urlStr = ""
		}
	}
	return out, requests, pagesSeen, nil
}

func (c *Client) ResolveURL(path string) (string, error) {
	var u *url.URL
	var err error
	if strings.HasPrefix(path, protocolHTTP) || strings.HasPrefix(path, protocolHTTPS) {
		u, err = url.Parse(path)
	} else {
		u, err = url.Parse(strings.TrimRight(c.BaseURL, "/") + path)
	}
	if err != nil {
		return "", err
	}
	q := u.Query()
	if q.Get(queryLimit) == "" {
		q.Set(queryLimit, fmt.Sprintf("%d", DefaultPageSize))
		u.RawQuery = q.Encode()
	}
	return u.String(), nil
}

func (c *Client) DoRequest(ctx context.Context, urlStr string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, urlStr, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set(headerAuthorization, tokenPrefix+c.Token)
	req.Header.Set(headerAccept, acceptApplicationJS)
	req.Header.Set(headerCacheControl, headerValueNoCache)
	req.Header.Set(headerPragma, headerValueNoCache)
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(body)))
	}
	return body, nil
}
