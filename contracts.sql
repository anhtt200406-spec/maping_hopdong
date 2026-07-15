-- 1. Hàm chuẩn hoá 
CREATE OR REPLACE FUNCTION normalize_code_sql(code TEXT) RETURNS TEXT AS $$
    SELECT NULLIF(
        regexp_replace(
            translate(
                upper(code),
                'ĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ',
                'DAAAAAAAAAAAAAAAAAAEEEEEEEEEEEIIIIIOOOOOOOOOOOOOOOOOUUUUUUUUUUUYYYYY'
            ),
            '[^A-Z0-9]', '', 'g'
        ),
        ''
    );
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION is_addendum_sql(contract_code TEXT, dinh_kem TEXT) RETURNS BOOLEAN AS $$
    SELECT (dinh_kem IS NOT NULL AND dinh_kem <> '')
        OR (contract_code ILIKE '%PLHD%' OR contract_code ILIKE '%/PL/%');
$$ LANGUAGE sql IMMUTABLE;

-- 2. Thêm cột sinh sẵn vào cả 2 bảng
ALTER TABLE contracts
    ADD COLUMN IF NOT EXISTS contract_code_norm TEXT
    GENERATED ALWAYS AS (normalize_code_sql(contract_code)) STORED;

ALTER TABLE contracts
    ADD COLUMN IF NOT EXISTS is_addendum_flag BOOLEAN
    GENERATED ALWAYS AS (is_addendum_sql(contract_code, dinh_kem_hop_dong_so)) STORED;

ALTER TABLE ct_contracts
    ADD COLUMN IF NOT EXISTS contract_code_norm TEXT
    GENERATED ALWAYS AS (normalize_code_sql(contract_code)) STORED;

-- 3.Index
CREATE INDEX IF NOT EXISTS idx_contracts_code_norm ON contracts (contract_code_norm);
CREATE INDEX IF NOT EXISTS idx_ct_contracts_code_norm ON ct_contracts (contract_code_norm);

-- 3 view báo cáo 
CREATE OR REPLACE VIEW vw_contracts_both AS
SELECT
    c.drive_link, 
    c.file_path, 
    c.contract_code 		AS drive_contract_code,
    ct.code 				AS ct_code, 
    ct.contract_code 		AS ct_contract_code, 
    ct.contract_path,
    c.contract_code_norm 	AS code_norm
FROM contracts c
JOIN ct_contracts ct ON c.contract_code_norm = ct.contract_code_norm
WHERE c.contract_code_norm IS NOT NULL
  AND NOT c.is_addendum_flag;

CREATE OR REPLACE VIEW vw_contracts_missing_in_urcard AS
SELECT 
	drive_link, 
	c.file_path, 
	c.contract_code			AS drive_contract_code,
	c.contract_code_norm 	AS code_norm
FROM contracts c
WHERE c.contract_code_norm IS NOT NULL
  AND NOT c.is_addendum_flag
  AND NOT EXISTS (
      SELECT 1 FROM ct_contracts ct WHERE ct.contract_code_norm = c.contract_code_norm
  );
SELECT * FROM vw_contracts_missing_in_urcard

CREATE OR REPLACE VIEW vw_contracts_missing_in_drive AS
SELECT 
	ct.code, 
	ct.contract_code, 
	ct.contract_path, 
	ct.contract_code_norm AS code_norm
FROM ct_contracts ct
WHERE ct.contract_code_norm IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM contracts c
      WHERE c.contract_code_norm = ct.contract_code_norm AND NOT c.is_addendum_flag
  );

-- 5. Kiểm tra lại 
SELECT count(*) FROM vw_contracts_both;
SELECT count(*) FROM vw_contracts_missing_in_urcard;
SELECT count(*) FROM vw_contracts_missing_in_drive;
SELECT * FROM vw_contracts_both
SELECT * FROM vw_contracts_missing_in_urcard;
SELECT count(*) FROM vw_contracts_both
SELECT count(*) FROM ct_contracts
	
	
	

