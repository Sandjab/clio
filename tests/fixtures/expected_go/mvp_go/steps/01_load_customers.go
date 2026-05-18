package steps

import (
	"context"
)

type LoadCustomersIn struct {
	File string `json:"file"`
}

type LoadCustomersOut struct {
	Customers []struct { Name string `json:"name"`; Revenue float64 `json:"revenue"` } `json:"customers"`
}

// LoadCustomers implements the 'load_customers' step.
func LoadCustomers(ctx context.Context, in LoadCustomersIn) (LoadCustomersOut, error) {
	panic("fill me in: load_customers")
}
