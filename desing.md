Here is the design and some planned features for better seismic attribute plotting I've in mind:
1. Input data in plain text or csv format. Each attribute is a column. First row is a header with attribute names
2. Any attribute or attribute combination can be used for plotting
3. Allow arithmetical operations on columns on the fly
4. Supported plot types:
   * Scatter and color scatter plots on rectangular and radial grids
   * Line graphs
   * Bar charts and density distributions
   * 2d density distributions
   * Rose diagram - polar plot of number of points within a bin on redial and angular axis
5. User might have multiple input files
6. Every plot should allow to add new layers from the same or different data file
7. Plot types should be composable, e.g overlaying density distribution on the bar chart or adding a line plot on top of scatter
8. Plots should be interactive - using Plotly/Holoviews library as a frontend
9. If huge number of points is rendered use Datashader for performance
10. Plot interactivity should be actually useful - user should be able not only to pan and zoom, but also select data, view annotations on hover etc.
11. Option to export selected points
12. Tech stack - uv to manage Python environment with ruff and ty for code checking. Data handling with pandas dataframes
13. Final deliverable - a standalone portable App Image  
