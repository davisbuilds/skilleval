def send(client, payload):
    """Return the remote client's acknowledgement."""
    return client.publish(payload)
