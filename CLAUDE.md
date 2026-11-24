Develop a 2D engine for optical layout to scale. Use imperial system

what it includes:

- optical breadboard
- standard Thorlabs mounts: FMP1, KM100, KM100CP, KM100BP, SM1ZB, CP35, KB1X1
- standard Thorlabs base mounts: PH2, BE1, CF125C...
- Combined units:
  - Base = PH2+BE1+CF125C           (base not mounted to holes)
  - KM100+Base                      (km-mirror)
  - KM100BP+KB1X1+Base              (mag-km-pellicle)
  - KM100CP+PH2                     (km-h-mirror)
  - FMP1+Base                       (mirror)
  - FMP1+PH2                        (h-mirror)
  - Collimator = [ SM1ZB+PH2 ] + [ some distance ] + CP35
- Base mounts and optics sit in different layers
- gaussion optics: specified source wavelength, wasit, locations, angle ==> defines a beam. The components interacts with/block the beams, beam parameters at differen location can be retrieved.  
- Mirror mounts and SM1ZB can be mounted in different orientations
- The UI allows users to drag/place/rotate the components, spawn/check a beam, measure on canvas. When components overlaps, highlight the illegal placement.
- The UI needs to be very fast/aesthetically good/easy to use/doesn't crash
- The component rendering needs to be fast. Since its 2D engine (looking down), the CAD file only need to be rendered once in to 2D image.
- The config can be saved/retrieved/modified easily


Claude may use the .venv, setup requirements.txt, .git, .gitignore

Claude should write the code in the most readable and organized way

Claude can decide how to implement the project as long as the requirements are realized

Claude stays in the project folder

Claude can use internet to look up for materials

Claude can add features if they helps make the project better
