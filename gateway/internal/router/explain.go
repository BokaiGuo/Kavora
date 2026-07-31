package router

import "encoding/json"

func Explain(decision Decision) ([]byte, error) { return json.Marshal(decision) }
