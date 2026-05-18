package steps

import (
	"context"
)

type LoadIn struct {
	File string `json:"file"`
}

type LoadOut struct {
	Customers []struct { Name string `json:"name"`; Revenue float64 `json:"revenue"` } `json:"customers"`
}

// Load implements the 'load' step.
func Load(ctx context.Context, in LoadIn) (LoadOut, error) {
	panic("fill me in: load")
}
