package steps

import (
	"context"
)

type LoadIn struct {
	File string `json:"file"`
}

type LoadOut struct {
	Items []string `json:"items"`
}

// Load implements the 'load' step.
func Load(ctx context.Context, in LoadIn) (LoadOut, error) {
	panic("fill me in: load")
}
