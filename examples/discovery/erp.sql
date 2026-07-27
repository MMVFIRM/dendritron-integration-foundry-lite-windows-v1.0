CREATE TABLE accounts (
    external_id VARCHAR(128) PRIMARY KEY,
    legal_name VARCHAR(255) NOT NULL,
    account_status VARCHAR(40) NOT NULL,
    primary_email VARCHAR(255),
    updated_at TIMESTAMP
);

CREATE TABLE account_audit (
    audit_id BIGINT PRIMARY KEY,
    external_id VARCHAR(128) NOT NULL REFERENCES accounts(external_id),
    event_type VARCHAR(80) NOT NULL,
    event_payload JSON,
    created_at TIMESTAMP NOT NULL
);
