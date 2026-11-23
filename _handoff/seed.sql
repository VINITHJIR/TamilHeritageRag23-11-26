-- =====================================================================
-- Level 1 seed data: 10 sites, 8 districts, mixed categories
-- NOTE: names/places are real; LGD codes (except state 33), taluk/local-body
-- mapping and coordinates marked NULL / verified=false MUST be checked against
-- lgdirectory.gov.in before production ingest.
-- =====================================================================

INSERT INTO categories (code, label_en, label_ta) VALUES
 ('temple',               'Temple',                 'கோயில்'),
 ('monument',             'Monument',               'நினைவுச்சின்னம்'),
 ('museum',               'Museum',                 'அருங்காட்சியகம்'),
 ('fort',                 'Fort',                   'கோட்டை'),
 ('palace',               'Palace',                 'அரண்மனை'),
 ('rock_cut',             'Rock-cut architecture',  'குடைவரை'),
 ('archaeological_site',  'Archaeological site',    'அகழாய்வுத் தளம்'),
 ('memorial',             'Memorial',               'நினைவகம்'),
 ('heritage_tourist_spot','Heritage tourist spot',  'பாரம்பரிய சுற்றுலாத் தலம்'),
 ('unesco',               'UNESCO World Heritage',  'யுனெஸ்கோ உலகப் பாரம்பரியம்'),
 ('asi_protected',        'ASI protected',          'இந்தியத் தொல்லியல் துறை பாதுகாப்பு');

-- ---------------- LOCATIONS ----------------
INSERT INTO locations (location_id, parent_id, level, name_en, name_ta, local_body_type, lgd_code, verified) VALUES
 (1,  NULL,'state',      'Tamil Nadu',     'தமிழ்நாடு',      NULL, 33,   TRUE),
 (2,  1,   'district',   'Thanjavur',      'தஞ்சாவூர்',      NULL, NULL, FALSE),
 (3,  1,   'district',   'Chengalpattu',   'செங்கல்பட்டு',   NULL, NULL, FALSE),
 (4,  1,   'district',   'Madurai',        'மதுரை',          NULL, NULL, FALSE),
 (5,  1,   'district',   'Erode',          'ஈரோடு',          NULL, NULL, FALSE),
 (6,  1,   'district',   'Chennai',        'சென்னை',         NULL, NULL, FALSE),
 (7,  1,   'district',   'Villupuram',     'விழுப்புரம்',    NULL, NULL, FALSE),
 (8,  1,   'district',   'Sivaganga',      'சிவகங்கை',       NULL, NULL, FALSE),
 (9,  1,   'district',   'Kanniyakumari',  'கன்னியாகுமரி',   NULL, NULL, FALSE),
 (10, 2,   'subdistrict','Thanjavur',      'தஞ்சாவூர்',      NULL, NULL, FALSE),
 (11, 3,   'subdistrict','Thirukalukundram','திருக்கழுக்குன்றம்',NULL,NULL, FALSE),
 (13, 5,   'subdistrict','Sathyamangalam', 'சத்தியமங்கலம்',  NULL, NULL, FALSE),
 (15, 7,   'subdistrict','Gingee',         'செஞ்சி',         NULL, NULL, FALSE),
 (16, 8,   'subdistrict','Thiruppuvanam',  'திருப்புவனம்',   NULL, NULL, FALSE),
 (17, 9,   'subdistrict','Agastheeswaram', 'அகஸ்தீஸ்வரம்',   NULL, NULL, FALSE),
 (20, 2,   'local_body', 'Thanjavur Corporation',       'தஞ்சாவூர் மாநகராட்சி', 'municipal_corporation', NULL, FALSE),
 (21, 3,   'local_body', 'Mamallapuram',                'மாமல்லபுரம்',          'town_panchayat',        NULL, FALSE),
 (22, 4,   'local_body', 'Madurai Corporation',         'மதுரை மாநகராட்சி',     'municipal_corporation', NULL, FALSE),
 (23, 6,   'local_body', 'Greater Chennai Corporation', 'பெருநகர சென்னை மாநகராட்சி','municipal_corporation', NULL, FALSE),
 (24, 7,   'local_body', 'Gingee',                      'செஞ்சி',               'town_panchayat',        NULL, FALSE),
 (25, 8,   'local_body', 'Keeladi',                     'கீழடி',                'village_panchayat',     NULL, FALSE),
 (26, 9,   'local_body', 'Kanniyakumari',               'கன்னியாகுமரி',         'town_panchayat',        NULL, FALSE);

-- ---------------- SITES (10 rows) ----------------
INSERT INTO sites (site_id, name_en, name_ta, primary_category, categories,
                   district_id, subdistrict_id, local_body_id, lat, lon,
                   period, dynasty, status, trust, origin) VALUES
 ('TN-TNJ-0001','Brihadeeswarar Temple','தஞ்சை பெரிய கோயில்','temple',
   '{temple,monument,unesco,asi_protected,heritage_tourist_spot}', 2,10,20, 10.782800,79.131800,
   '11th century CE','Chola','verified','official','curated'),
 ('TN-CGL-0001','Group of Monuments at Mamallapuram','மாமல்லபுரம் நினைவுச்சின்னங்கள்','monument',
   '{monument,rock_cut,temple,unesco,asi_protected,heritage_tourist_spot}', 3,11,21, 12.616500,80.199300,
   '7th–8th century CE','Pallava','verified','official','curated'),
 ('TN-MDU-0001','Meenakshi Amman Temple','மீனாட்சி அம்மன் கோயில்','temple',
   '{temple,heritage_tourist_spot}', 4,NULL,22, 9.919500,78.119300,
   '16th–17th century CE (major rebuilding)','Madurai Nayak','verified','multi_source','curated'),
 ('TN-ERD-0001','Bannari Amman Temple','பண்ணாரி அம்மன் கோயில்','temple',
   '{temple}', 5,13,NULL, NULL,NULL,
   NULL,NULL,'pending','single_source','curated'),
 ('TN-CHN-0001','Government Museum, Chennai','அரசு அருங்காட்சியகம், சென்னை','museum',
   '{museum,heritage_tourist_spot}', 6,NULL,23, 13.069700,80.256300,
   'Established 1851 CE',NULL,'verified','official','curated'),
 ('TN-VPM-0001','Gingee Fort','செஞ்சிக் கோட்டை','fort',
   '{fort,monument,asi_protected,heritage_tourist_spot}', 7,15,24, NULL,NULL,
   'Vijayanagara–Nayak period','Gingee Nayaks','verified','multi_source','curated'),
 ('TN-SVG-0001','Keeladi Excavation Site','கீழடி அகழாய்வுத் தளம்','archaeological_site',
   '{archaeological_site,museum,heritage_tourist_spot}', 8,16,25, NULL,NULL,
   'Sangam era',NULL,'verified','official','curated'),
 ('TN-TNJ-0002','Thanjavur Maratha Palace','தஞ்சாவூர் மராட்டிய அரண்மனை','palace',
   '{palace,museum,heritage_tourist_spot}', 2,10,20, NULL,NULL,
   '16th–19th century CE','Thanjavur Nayaks / Marathas','verified','multi_source','curated'),
 ('TN-KNK-0001','Vattakottai Fort','வட்டக்கோட்டை','fort',
   '{fort,heritage_tourist_spot}', 9,17,NULL, NULL,NULL,
   '18th century CE','Travancore','pending','single_source','curated'),
 ('TN-KNK-0002','Vivekananda Rock Memorial','விவேகானந்தர் பாறை நினைவகம்','memorial',
   '{memorial,heritage_tourist_spot}', 9,17,26, 8.078000,77.555000,
   '1970 CE',NULL,'verified','multi_source','curated');

-- ---------------- ALIASES ----------------
INSERT INTO site_aliases (site_id, alias, lang) VALUES
 ('TN-TNJ-0001','Big Temple','en'), ('TN-TNJ-0001','Tanjore Temple','en'),
 ('TN-TNJ-0001','Rajarajeswaram','en'), ('TN-TNJ-0001','பெருவுடையார் கோயில்','ta'),
 ('TN-TNJ-0001','thanjavur periya kovil','ta-Latn'),
 ('TN-CGL-0001','Mahabalipuram','en'), ('TN-CGL-0001','Shore Temple','en'),
 ('TN-CGL-0001','Pancha Rathas','en'), ('TN-CGL-0001','mamallapuram kadarkarai kovil','ta-Latn'),
 ('TN-MDU-0001','Madurai Meenakshi Temple','en'), ('TN-MDU-0001','meenakshi amman kovil','ta-Latn'),
 ('TN-ERD-0001','Bannari Mariamman','en'), ('TN-ERD-0001','ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ','kn'),
 ('TN-ERD-0001','bannari amman kovil','ta-Latn'),
 ('TN-CHN-0001','Egmore Museum','en'), ('TN-CHN-0001','Madras Museum','en'),
 ('TN-VPM-0001','Senji Fort','en'), ('TN-VPM-0001','Troy of the East','en'),
 ('TN-SVG-0001','Keezhadi','en'), ('TN-SVG-0001','keeladi agalaivu','ta-Latn'),
 ('TN-TNJ-0002','Saraswathi Mahal','en'), ('TN-TNJ-0002','Thanjavur Palace','en'),
 ('TN-KNK-0001','Vattakottai','en'),
 ('TN-KNK-0002','Vivekananda Mandapam','en'), ('TN-KNK-0002','vivekanandar paarai','ta-Latn');

-- ---------------- SOURCES ----------------
INSERT INTO sources (source_id, url, domain, tier) VALUES
 (1,'https://whc.unesco.org/en/list/250','whc.unesco.org','A'),
 (2,'https://whc.unesco.org/en/list/249','whc.unesco.org','A'),
 (3,'https://asi.nic.in/','asi.nic.in','A'),
 (4,'https://hrce.tn.gov.in/','hrce.tn.gov.in','A'),
 (5,'https://www.tnarch.gov.in/','tnarch.gov.in','A'),
 (6,'https://en.wikipedia.org/','en.wikipedia.org','B'),
 (7,'https://ta.wikipedia.org/','ta.wikipedia.org','B'),
 (8,'manual://team-curated','team','C');
SELECT setval('sources_source_id_seq', 8);

-- ---------------- FACTS (field-level provenance, sample) ----------------
INSERT INTO site_facts (site_id, field, value, lang, source_id, score, status) VALUES
 ('TN-TNJ-0001','builder','Rajaraja Chola I','en',1,1.000,'verified'),
 ('TN-TNJ-0001','completed','c. 1010 CE','en',1,1.000,'verified'),
 ('TN-CGL-0001','unesco_inscribed','1984','en',2,1.000,'verified'),
 ('TN-TNJ-0001','unesco_inscribed','1987','en',1,1.000,'verified'),
 ('TN-ERD-0001','festival','Kundam (fire-walking)','en',8,0.500,'pending');
