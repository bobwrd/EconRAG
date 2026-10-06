# Development library (ROADMAP Phase 3)

Approved Oct 6 2026; all 15 are in `docs/` and ingested (#2 had to be saved from a browser: its
host serves scripts a challenge page, so `setup_assistant.py` asks other users to do the same).
#8 turned out to be 37 MB. Links checked 2026-10-06 (HTTP 200, `application/pdf`,
no paywall, unless noted). Total ≈ 33 MB for the 13 with known sizes (≈ 35-40 MB with #2 and #8).

| # | Citation | Why useful | PDF | OA / license | Size |
|---|---|---|---|---|---|
| 1 | Banerjee, Karlan & Zinman (2015). Six Randomized Evaluations of Microcredit: Introduction and Further Steps. *AEJ: Applied* 7(1) | "Does microfinance work?" — modest, not transformative | [MIT](https://economics.mit.edu/sites/default/files/publications/Six%20Randomized%20Evaluations%20of%20Microcredit.pdf) | author copy | 1.40 MB |
| 2 | Haushofer & Shapiro (2016). The Short-term Impact of Unconditional Cash Transfers to the Poor: Experimental Evidence from Kenya. *QJE* 131(4) | The GiveDirectly RCT | [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7575201/pdf/nihms-1632237.pdf) | NIH author manuscript; **needs a browser download** (scripts get a JS challenge) | ? |
| 3 | Egger, Haushofer, Miguel, Niehaus & Walker (2019). General Equilibrium Effects of Cash Transfers: Experimental Evidence from Kenya. NBER WP 26600 (*Econometrica* 2022) | Local multiplier, minimal inflation, spillovers | [NBER](https://www.nber.org/system/files/working_papers/w26600/w26600.pdf) | NBER WP | 0.57 MB |
| 4 | Bastagli et al. (2016). *Cash transfers: what does the evidence say?* ODI | Synthesis of 165 studies / 56 programmes | [ODI](https://cdn.odi.org/media/documents/11316.pdf) | free, non-commercial reuse (not CC) | 6.93 MB |
| 5 | Hamory, Miguel, Walker, Kremer & Baird (2021). Twenty-Year Economic Impacts of Deworming. *PNAS* 118(14) | Long-run deworming follow-up | [Berkeley](https://emiguel.econ.berkeley.edu/wordpress/wp-content/uploads/2020/07/Twenty-year-economic-impacts-of-deworming.pdf) | author copy (2020 WP version) | 1.12 MB |
| 6 | Banerjee, Duflo, Goldberg, Karlan et al. (2015). A Multifaceted Program Causes Lasting Progress for the Very Poor: Evidence from Six Countries. *Science* 348 | Six-country graduation RCT | [MIT](https://economics.mit.edu/sites/default/files/2022-08/science.1260799.pdf) | author copy | 0.94 MB |
| 7 | Hanna & Olken (2018). Universal Basic Incomes versus Targeted Transfers. *JEP* 32(4) | UBI vs targeting in poor countries | [MIT](https://economics.mit.edu/sites/default/files/publications/JEP%20UBI%20vs%20Targeted%20Final.pdf) | JEP free; author copy | 0.94 MB |
| 8 | Global Education Evidence Advisory Panel (2023). Cost-Effective Approaches to Improve Global Learning ("Smart Buys"). World Bank/FCDO/UNICEF | Ranked "what works" in education | [World Bank](https://documents1.worldbank.org/curated/en/099420106132331608/pdf/IDU-977f73d7-22b1-4777-980c-c5a14598eef8.pdf) | license not confirmed (likely CC BY 3.0 IGO) | ? |
| 9 | Duflo (2012). Women Empowerment and Economic Development. *JEL* 50(4); NBER WP 17702 | Gender and development survey | [NBER](https://www.nber.org/system/files/working_papers/w17702/w17702.pdf) | NBER WP | 0.32 MB |
| 10 | World Bank (2024). *Poverty, Prosperity, and Planet Report 2024* | Flagship poverty/inequality numbers (uses the old $2.15 line — see #11) | [OKR](https://openknowledge.worldbank.org/server/api/core/bitstreams/ea01aa05-2f15-4ebe-aa94-0e4a8cc82406/content) (Overview only: [3.2 MB](https://openknowledge.worldbank.org/server/api/core/bitstreams/f75dd18d-4e3f-44f9-b455-7f0d8e189609/content)) | CC BY 3.0 IGO | 9.35 MB |
| 11 | Foster, Jolliffe, Lara Ibarra, Lakner & Tetteh-Baah (2025). Global Poverty Revisited Using 2021 PPPs and New Data on Consumption. WB Policy Research WP 11137 | The paper behind the $3.00 line | [OKR](https://openknowledge.worldbank.org/server/api/core/bitstreams/26fba851-6e59-452f-b8aa-5186eb32fb8b/content) | CC BY 3.0 IGO | 3.78 MB |
| 12 | Acemoglu, Johnson & Robinson (2005). Institutions as a Fundamental Cause of Long-Run Growth. *Handbook of Economic Growth*; NBER WP 10481 | "Why are some countries rich?" | [NBER](https://www.nber.org/system/files/working_papers/w10481/w10481.pdf) | NBER WP | 0.78 MB |
| 13 | Commission on Growth and Development (2008). *The Growth Report*. World Bank | 13 high-growth economies, policy lessons | [OKR](https://openknowledge.worldbank.org/server/api/core/bitstreams/df194a38-cb6f-5553-8fd8-e48a8a7c9574/content) | CC BY 3.0 IGO | 5.48 MB |
| 14 | McMillan & Rodrik (2011). Globalization, Structural Change and Productivity Growth. NBER WP 17143 | Structural transformation | [NBER](https://www.nber.org/system/files/working_papers/w17143/w17143.pdf) | NBER WP | 0.36 MB |
| 15 | Clemens (2011). Economics and Emigration: Trillion-Dollar Bills on the Sidewalk? CGD WP 264 (*JEP* 25(3)) | Gains from migration | [CGD](https://www.cgdev.org/sites/default/files/1425376_file_Clemens_Economics_and_Emigration_FINAL.pdf) | CGD WP (free) | 0.68 MB |

Alternates checked, not picked: WDR 2023 *Migrants, Refugees, and Societies* (14.4 MB, Overview 0.6 MB);
Banerjee, Duflo & Sharma, Long-term Effects of the Targeting the Ultra Poor Program, NBER w28074 (0.31 MB).
Gaps: J-PAL policy insights are web pages, not PDFs; the Cochrane deworming review's OA status unchecked.

Before ingesting: World Bank links end in `/content`, so save under explicit file names, and attach
title/authors/year to each chunk (ROADMAP Phase 3) so citations are real references, not file names.
Adding ~15 PDFs (some 300 pages) grows `ingest.py`'s full rebuild well past today's ~40 s.
