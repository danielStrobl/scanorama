from annoy import AnnoyIndex
from intervaltree import IntervalTree
from itertools import cycle, islice
import numpy as np
import operator
import random
from scipy.sparse import csr_matrix, vstack
from sklearn.manifold import TSNE
from sklearn.metrics.pairwise import rbf_kernel, euclidean_distances
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import normalize
import sys

from .t_sne_approx import TSNEApprox
from .utils import plt, dispersion, reduce_dimensionality
from .utils import visualize_cluster, visualize_expr

np.random.seed(0)
random.seed(0)

# Default parameters.
ALPHA = 0.10
APPROX = True
DIMRED = 100
HVG = 0
KNN = 20
N_ITER = 500
PERPLEXITY = 1200
SIGMA = 150
VERBOSE = 2

# Visualize a scatter plot with cluster labels in the
# `cluster' variable.
def plot_clusters(coords, clusters, s=1):
    if coords.shape[0] != clusters.shape[0]:
        sys.stderr.write(
            'Error: mismatch, {} cells, {} labels\n'
            .format(coords.shape[0], clusters.shape[0])
        )
        exit(1)

    colors = np.array(
        list(islice(cycle([
            '#377eb8', '#ff7f00', '#4daf4a',
            '#f781bf', '#a65628', '#984ea3',
            '#999999', '#e41a1c', '#dede00',
            '#ffe119', '#e6194b', '#ffbea3',
            '#911eb4', '#46f0f0', '#f032e6',
            '#d2f53c', '#008080', '#e6beff',
            '#aa6e28', '#800000', '#aaffc3',
            '#808000', '#ffd8b1', '#000080',
            '#808080', '#fabebe', '#a3f4ff'
        ]), int(max(clusters) + 1)))
    )
        
    plt.figure()
    plt.scatter(coords[:, 0], coords[:, 1],
                c=colors[clusters], s=s)

# Put datasets into a single matrix with the intersection of all genes.
def merge_datasets(datasets, genes, ds_names=None, verbose=True):
    # Find genes in common.
    keep_genes = set()
    for idx, gene_list in enumerate(genes):
        if len(keep_genes) == 0:
            keep_genes = set(gene_list)
        else:
            keep_genes &= set(gene_list)
        if not ds_names is None and verbose:
            print('After {}: {} genes'.format(ds_names[idx], len(keep_genes)))
        if len(keep_genes) == 0:
            print('Error: No genes found in all datasets, exiting...')
            exit(1)
    if verbose:
        print('Found {} genes among all datasets'
              .format(len(keep_genes)))

    # Only keep genes in common.
    ret_genes = np.array(sorted(keep_genes))
    for i in range(len(datasets)):
        # Remove duplicate genes.
        uniq_genes, uniq_idx = np.unique(genes[i], return_index=True)
        datasets[i] = datasets[i][:, uniq_idx]

        # Do gene filtering.
        gene_sort_idx = np.argsort(uniq_genes)
        gene_idx = [ idx for idx in gene_sort_idx
                     if uniq_genes[idx] in keep_genes ]
        datasets[i] = datasets[i][:, gene_idx]
        assert(np.array_equal(uniq_genes[gene_idx], ret_genes))

    return datasets, ret_genes

# Do batch correction on the data.
def correct(datasets_full, genes_list, hvg=HVG, verbose=VERBOSE,
            sigma=SIGMA, approx=APPROX, alpha=ALPHA, ds_names=None,
            return_dimred=False, batch_size=None):
    datasets, genes = merge_datasets(datasets_full, genes_list,
                                     ds_names=ds_names)
    datasets_dimred, genes = process_data(datasets, genes, hvg=hvg)
    
    datasets_dimred = assemble(
        datasets_dimred, # Assemble in low dimensional space.
        expr_datasets=datasets, # Modified in place.
        verbose=verbose, knn=KNN, sigma=sigma, approx=approx,
        alpha=alpha, ds_names=ds_names, batch_size=batch_size
    )

    if return_dimred:
        return datasets_dimred, datasets, genes

    return datasets, genes

# Randomized SVD.
def dimensionality_reduce(datasets, dimred=DIMRED):
    X = vstack(datasets)
    X = reduce_dimensionality(X, dim_red_k=dimred)
    datasets_dimred = []
    base = 0
    for ds in datasets:
        datasets_dimred.append(X[base:(base + ds.shape[0]), :])
        base += ds.shape[0]
    return datasets_dimred

# Normalize and reduce dimensionality.
def process_data(datasets, genes, hvg=HVG, dimred=DIMRED, verbose=False):
    # Only keep highly variable genes
    if hvg > 0 and hvg < len(genes):
        if verbose:
            print('Highly variable filter...')
        X = vstack(datasets)
        disp = dispersion(X)
        top_genes = set(genes[
            list(reversed(np.argsort(disp)))[:HVG]
        ])
        for i in range(len(datasets)):
            gene_idx = [ idx for idx, g_i in enumerate(genes)
                         if g_i in top_genes ]
            datasets[i] = datasets[i][:, gene_idx]
        genes = np.array(sorted(top_genes))

    # Normalize.
    if verbose:
        print('Normalizing...')
    for i, ds in enumerate(datasets):
        datasets[i] = normalize(ds, axis=1)
    
    # Compute compressed embedding.
    if dimred > 0:
        if verbose:
            print('Reducing dimension...')
        datasets_dimred = dimensionality_reduce(datasets)
        if verbose:
            print('Done processing.')
        return datasets_dimred, genes

    if verbose:
        print('Done processing.')

    return datasets, genes

# Plot t-SNE visualization.
def visualize(assembled, labels, namespace, data_names,
              gene_names=None, gene_expr=None, genes=None,
              n_iter=N_ITER, perplexity=PERPLEXITY, verbose=VERBOSE,
              learn_rate=200., early_exag=12., embedding=None,
              shuffle_ds=False, size=1, multicore_tsne=True,
              image_suffix='.svg'):
    # Fit t-SNE.
    if embedding is None:
        try:
            from MulticoreTSNE import MulticoreTSNE
            tsne = MulticoreTSNE(
                n_iter=n_iter, perplexity=perplexity,
                verbose=verbose, random_state=69,
                learning_rate=learn_rate,
                early_exaggeration=early_exag,
                n_jobs=40
            )
        except ImportError:
            multicore_tsne = False

        if not multicore_tsne:
            tsne = TSNEApprox(
                n_iter=n_iter, perplexity=perplexity,
                verbose=verbose, random_state=69,
                learning_rate=learn_rate,
                early_exaggeration=early_exag
            )

        tsne.fit(np.concatenate(assembled))
        embedding = tsne.embedding_

    if shuffle_ds:
        rand_idx = range(embedding.shape[0])
        random.shuffle(list(rand_idx))
        embedding = embedding[rand_idx, :]
        labels = labels[rand_idx]

    # Plot clusters together.
    plot_clusters(embedding, labels, s=size)
    plt.title(('Panorama ({} iter, perplexity: {}, sigma: {}, ' +
               'knn: {}, hvg: {}, dimred: {}, approx: {})')
              .format(n_iter, perplexity, SIGMA, KNN, HVG,
                      DIMRED, APPROX))
    plt.savefig(namespace + image_suffix, dpi=500)

    # Plot clusters individually.
    if not shuffle_ds:
        for i in range(len(data_names)):
            visualize_cluster(embedding, i, labels,
                              cluster_name=data_names[i], size=size,
                              viz_prefix=namespace,
                              image_suffix=image_suffix)

    # Plot gene expression levels.
    if (not gene_names is None) and \
       (not gene_expr is None) and \
       (not genes is None):
        if shuffle_ds:
            gene_expr = gene_expr[rand_idx, :]
        for gene_name in gene_names:
            visualize_expr(gene_expr, embedding,
                           genes, gene_name, size=size,
                           viz_prefix=namespace,
                           image_suffix=image_suffix)

    return embedding

# Exact nearest neighbors search.
def nn(ds1, ds2, knn=KNN, metric_p=2):
    # Find nearest neighbors of first dataset.
    nn_ = NearestNeighbors(knn, p=metric_p)
    nn_.fit(ds2)
    ind = nn_.kneighbors(ds1, return_distance=False)

    match = set()
    for a, b in zip(range(ds1.shape[0]), ind):
        for b_i in b:
            match.add((a, b_i))

    return match

# Approximate nearest neighbors using locality sensitive hashing.
def nn_approx(ds1, ds2, knn=KNN, metric='manhattan', n_trees=10):
    # Build index.
    a = AnnoyIndex(ds2.shape[1], metric=metric)
    for i in range(ds2.shape[0]):
        a.add_item(i, ds2[i, :])
    a.build(n_trees)

    # Search index.
    ind = []
    for i in range(ds1.shape[0]):
        ind.append(a.get_nns_by_vector(ds1[i, :], knn, search_k=-1))
    ind = np.array(ind)

    # Match.
    match = set()
    for a, b in zip(range(ds1.shape[0]), ind):
        for b_i in b:
            match.add((a, b_i))

    return match

# Find mutual nearest neighbors.
def mnn(ds1, ds2, knn=KNN, approx=APPROX):
    # Find nearest neighbors in first direction.
    if approx:
        match1 = nn_approx(ds1, ds2, knn=knn)
    else:
        match1 = nn(ds1, ds2, knn=knn)

    # Find nearest neighbors in second direction.
    if approx:
        match2 = nn_approx(ds2, ds1, knn=knn)
    else:
        match2 = nn(ds2, ds1, knn=knn)
        
    # Compute mutual nearest neighbors.
    mutual = match1 & set([ (b, a) for a, b in match2 ])

    return mutual

# Visualize alignment between two datasets.
def plot_mapping(curr_ds, curr_ref, ds_ind, ref_ind):
    tsne = TSNE(n_iter=400, verbose=VERBOSE, random_state=69)
    
    tsne.fit(curr_ds)
    plt.figure()
    coords_ds = tsne.embedding_[:, :]
    coords_ds[:, 1] += 100
    plt.scatter(coords_ds[:, 0], coords_ds[:, 1])
    
    tsne.fit(curr_ref)
    coords_ref = tsne.embedding_[:, :]
    plt.scatter(coords_ref[:, 0], coords_ref[:, 1])

    x_list, y_list = [], []
    for r_i, c_i in zip(ds_ind, ref_ind):
        x_list.append(coords_ds[r_i, 0])
        x_list.append(coords_ref[c_i, 0])
        x_list.append(None)
        y_list.append(coords_ds[r_i, 1])
        y_list.append(coords_ref[c_i, 1])
        y_list.append(None)
    plt.plot(x_list, y_list, 'b-', alpha=0.3)
    plt.show()
    

# Populate a table (in place) that stores mutual nearest neighbors
# between datasets.
def fill_table(table, i, curr_ds, datasets, base_ds=0,
               knn=KNN, approx=APPROX):
    curr_ref = np.concatenate(datasets)
    if approx:
        match = nn_approx(curr_ds, curr_ref, knn=knn)
    else:
        match = nn(curr_ds, curr_ref, knn=knn, metric_p=1)

    # Build interval tree.
    itree_ds_idx = IntervalTree()
    itree_pos_base = IntervalTree()
    pos = 0
    for j in range(len(datasets)):
        n_cells = datasets[j].shape[0]
        itree_ds_idx[pos:(pos + n_cells)] = base_ds + j
        itree_pos_base[pos:(pos + n_cells)] = pos
        pos += n_cells
    
    # Store all mutual nearest neighbors between datasets.
    for d, r in match:
        interval = itree_ds_idx[r]
        assert(len(interval) == 1)
        j = interval.pop().data
        interval = itree_pos_base[r]
        assert(len(interval) == 1)
        base = interval.pop().data
        if not (i, j) in table:
            table[(i, j)] = set()
        table[(i, j)].add((d, r - base))
        assert(r - base >= 0)

# Fill table of alignment scores.
def find_alignments_table(datasets, knn=KNN, approx=APPROX,
                          verbose=VERBOSE, prenormalized=False):
    if not prenormalized:
        datasets = [ normalize(ds, axis=1) for ds in datasets ]
    
    table = {}
    for i in range(len(datasets)):
        if len(datasets[:i]) > 0:
            fill_table(table, i, datasets[i], datasets[:i], knn=knn,
                       approx=approx)
        if len(datasets[i+1:]) > 0:
            fill_table(table, i, datasets[i], datasets[i+1:],
                       knn=knn, base_ds=i+1, approx=approx)

    # Count all mutual nearest neighbors between datasets.
    matches = {}
    table1 = {}
    table_print = np.zeros((len(datasets), len(datasets)))
    for i in range(len(datasets)):
        for j in range(len(datasets)):
            if i >= j:
                continue
            if not (i, j) in table or not (j, i) in table:
                continue
            match_ij = table[(i, j)]
            match_ji = set([ (b, a) for a, b in table[(j, i)] ])
            matches[(i, j)] = match_ij & match_ji
            table1[(i, j)] = (max(
                float(len(set([ idx for idx, _ in matches[(i, j)] ]))) /
                datasets[i].shape[0],
                float(len(set([ idx for _, idx in matches[(i, j)] ]))) /
                datasets[j].shape[0]
            ))
            table_print[i, j] += table1[(i, j)]
    if verbose > 1:
        print(table_print)

    return table1, table_print, matches
    
# Find the matching pairs of cells between datasets.
def find_alignments(datasets, knn=KNN, approx=APPROX, verbose=VERBOSE,
                    alpha=ALPHA, prenormalized=False):
    table1, _, matches = find_alignments_table(
        datasets, knn=knn, approx=approx, verbose=verbose,
        prenormalized=prenormalized
    )

    alignments = [ (i, j) for (i, j), val in reversed(
        sorted(table1.items(), key=operator.itemgetter(1))
    ) if val > alpha ]

    return alignments, matches

# Find connections between datasets to identify panoramas.
def connect(datasets, knn=KNN, approx=APPROX, alpha=ALPHA,
            verbose=VERBOSE):
    # Find alignments.
    alignments, _ = find_alignments(
        datasets, knn=knn, approx=approx, alpha=alpha,
        verbose=verbose
    )
    if verbose:
        print(alignments)
    
    panoramas = []
    connected = set()
    for i, j in alignments:
        # See if datasets are involved in any current panoramas.
        panoramas_i = [ panoramas[p] for p in range(len(panoramas))
                        if i in panoramas[p] ]
        assert(len(panoramas_i) <= 1)
        panoramas_j = [ panoramas[p] for p in range(len(panoramas))
                        if j in panoramas[p] ]
        assert(len(panoramas_j) <= 1)
        
        if len(panoramas_i) == 0 and len(panoramas_j) == 0:
            panoramas.append([ i ])
            panoramas_i = [ panoramas[-1] ]
            
        if len(panoramas_i) == 0:
            panoramas_j[0].append(i)
        elif len(panoramas_j) == 0:
            panoramas_i[0].append(j)
        elif panoramas_i[0] != panoramas_j[0]:
            panoramas_i[0] += panoramas_j[0]
            panoramas.remove(panoramas_j[0])

        connected.add(i)
        connected.add(j)

    for i in range(len(datasets)):
        if not i in connected:
            panoramas.append([ i ])

    return panoramas

# To reduce memory usage, split bias computation into batches.
def batch_bias(curr_ds, match_ds, bias, batch_size=None, sigma=SIGMA):
    if batch_size is None:
        weights = rbf_kernel(curr_ds, match_ds, gamma=0.5*sigma)
        avg_bias = np.dot(weights, bias) / \
                   np.tile(np.sum(weights, axis=1),
                           (curr_ds.shape[1], 1)).T
        return avg_bias
    
    base = 0
    avg_bias = np.zeros(curr_ds.shape)
    denom = np.zeros(curr_ds.shape[0])
    while base < match_ds.shape[0]:
        batch_idx = range(
            base, min(base + batch_size, match_ds.shape[0])
        )
        weights = rbf_kernel(curr_ds, match_ds[batch_idx, :],
                             gamma=0.5*sigma)
        avg_bias += np.dot(weights, bias[batch_idx, :])
        denom += np.sum(weights, axis=1)
        base += batch_size

    avg_bias /= np.tile(denom, (curr_ds.shape[1], 1)).T

    return avg_bias

# Compute nonlinear translation vectors between dataset
# and a reference.
def transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma, cn=False,
              batch_size=None):
    # Compute the matching.
    match_ds = curr_ds[ds_ind, :]
    match_ref = curr_ref[ref_ind, :]
    bias = match_ref - match_ds
    if cn:
        match_ds = match_ds.toarray()
        curr_ds = curr_ds.toarray()
        bias = bias.toarray()

    avg_bias = batch_bias(curr_ds, match_ds, bias, sigma=sigma,
                          batch_size=batch_size)
    if cn:
        avg_bias = csr_matrix(avg_bias)
    
    return avg_bias
    
# Finds alignments between datasets and uses them to construct
# panoramas. "Merges" datasets by correcting gene expression
# values.
def assemble(datasets, verbose=VERBOSE, view_match=False, knn=KNN,
             sigma=SIGMA, approx=APPROX, alpha=ALPHA, expr_datasets=None,
             ds_names=None, batch_size=None, realign=False):
    if len(datasets) == 1:
        return datasets
    
    alignments, matches = find_alignments(datasets, knn=knn, approx=approx,
                                          alpha=alpha, verbose=verbose)
    
    ds_assembled = {}
    panoramas = []
    for i, j in alignments:
        if verbose:
            if ds_names is None:
                print('Processing datasets {}'.format((i, j)))
            else:
                print('Processing datasets {} <=> {}'.
                      format(ds_names[i], ds_names[j]))
        
        # Only consider a dataset a fixed amount of times.
        if not i in ds_assembled:
            ds_assembled[i] = 0
        ds_assembled[i] += 1
        if not j in ds_assembled:
            ds_assembled[j] = 0
        ds_assembled[j] += 1
        if ds_assembled[i] > 3 and ds_assembled[j] > 3:
            continue
                
        # See if datasets are involved in any current panoramas.
        panoramas_i = [ panoramas[p] for p in range(len(panoramas))
                        if i in panoramas[p] ]
        assert(len(panoramas_i) <= 1)
        panoramas_j = [ panoramas[p] for p in range(len(panoramas))
                        if j in panoramas[p] ]
        assert(len(panoramas_j) <= 1)
        
        if len(panoramas_i) == 0 and len(panoramas_j) == 0:
            if datasets[i].shape[0] < datasets[j].shape[0]:
                i, j = j, i
            panoramas.append([ i ])
            panoramas_i = [ panoramas[-1] ]

        # Map dataset i to panorama j.
        if len(panoramas_i) == 0:
            curr_ds = datasets[i]
            curr_ref = np.concatenate([ datasets[p] for p in panoramas_j[0] ])
            
            match = []
            base = 0
            for p in panoramas_j[0]:
                if i < p and (i, p) in matches:
                    match.extend([ (a, b + base) for a, b in matches[(i, p)] ])
                elif i > p and (p, i) in matches:
                    match.extend([ (b, a + base) for a, b in matches[(p, i)] ])
                base += datasets[p].shape[0]

            ds_ind = [ a for a, _ in match ]
            ref_ind = [ b for _, b in match ]
                    
            bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                             batch_size=batch_size)
            datasets[i] = curr_ds + bias
            
            if expr_datasets:
                curr_ds = expr_datasets[i]
                curr_ref = vstack([ expr_datasets[p]
                                    for p in panoramas_j[0] ])
                bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                                 cn=True, batch_size=batch_size)
                expr_datasets[i] = curr_ds + bias
            
            panoramas_j[0].append(i)
            
        # Map dataset j to panorama i.
        elif len(panoramas_j) == 0:
            curr_ds = datasets[j]
            curr_ref = np.concatenate([ datasets[p] for p in panoramas_i[0] ])
            
            match = []
            base = 0
            for p in panoramas_i[0]:
                if j < p and (j, p) in matches:
                    match.extend([ (a, b + base) for a, b in matches[(j, p)] ])
                elif j > p and (p, j) in matches:
                    match.extend([ (b, a + base) for a, b in matches[(p, j)] ])
                base += datasets[p].shape[0]
                
            ds_ind = [ a for a, _ in match ]
            ref_ind = [ b for _, b in match ]
            
            bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                             batch_size=batch_size)
            datasets[j] = curr_ds + bias
            
            if expr_datasets:
                curr_ds = expr_datasets[j]
                curr_ref = vstack([ expr_datasets[p]
                                    for p in panoramas_i[0] ])
                bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                                 cn=True, batch_size=batch_size)
                expr_datasets[j] = curr_ds + bias
                
            panoramas_i[0].append(j)

        # Merge two panoramas together.
        else:
            curr_ds = np.concatenate([ datasets[p] for p in panoramas_i[0] ])
            curr_ref = np.concatenate([ datasets[p] for p in panoramas_j[0] ])

            # Find base indices into each panorama.
            base_i = 0
            for p in panoramas_i[0]:
                if p == i: break
                base_i += datasets[p].shape[0]
            base_j = 0
            for p in panoramas_j[0]:
                if p == j: break
                base_j += datasets[p].shape[0]
            
            # Find matching indices.
            match = []
            base = 0
            for p in panoramas_i[0]:
                if p == i and j < p and (j, p) in matches:
                    match.extend([ (b + base, a + base_j)
                                   for a, b in matches[(j, p)] ])
                elif p == i and j > p and (p, j) in matches:
                    match.extend([ (a + base, b + base_j)
                                   for a, b in matches[(p, j)] ])
                base += datasets[p].shape[0]
            base = 0
            for p in panoramas_j[0]:
                if p == j and i < p and (i, p) in matches:
                    match.extend([ (a + base_i, b + base)
                                   for a, b in matches[(i, p)] ])
                elif p == j and i > p and (p, i) in matches:
                    match.extend([ (b + base_i, a + base)
                                   for a, b in matches[(p, i)] ])
                base += datasets[p].shape[0]
                
            ds_ind = [ a for a, _ in match ]
            ref_ind = [ b for _, b in match ]
            
            # Apply transformation to entire panorama.
            bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                             batch_size=batch_size)
            curr_ds += bias
            base = 0
            for p in panoramas_i[0]:
                n_cells = datasets[p].shape[0]
                datasets[p] = curr_ds[base:(base + n_cells), :]
                base += n_cells
            
            if realign and not expr_datasets is None:
                # Realignment may improve quality of batch correction at cost
                # of potentially higher memory usage.
                curr_ds = vstack([ expr_datasets[p]
                                   for p in panoramas_i[0] ])
                curr_ref = vstack([ expr_datasets[p]
                                    for p in panoramas_j[0] ])
                bias = transform(curr_ds, curr_ref, ds_ind, ref_ind, sigma,
                                 cn=True, batch_size=batch_size)
                curr_ds += bias
                base = 0
                for p in panoramas_i[0]:
                    n_cells = expr_datasets[p].shape[0]
                    expr_datasets[p] = curr_ds[base:(base + n_cells), :]
                    base += n_cells
                
            # Merge panoramas i and j and delete one.
            if panoramas_i[0] != panoramas_j[0]:
                panoramas_i[0] += panoramas_j[0]
                panoramas.remove(panoramas_j[0])

        # Visualize.
        if view_match:
            plot_mapping(curr_ds, curr_ref, ds_ind, ref_ind)

    return datasets

# Non-optimal dataset assembly. Simply accumulate datasets into a
# reference.
def assemble_accum(datasets, verbose=VERBOSE, knn=KNN, sigma=SIGMA,
                   approx=APPROX, batch_size=None):
    if len(datasets) == 1:
        return datasets
    
    for i in range(len(datasets) - 1):
        j = i + 1
        
        if verbose:
            print('Processing datasets {}'.format((i, j)))

        ds1 = datasets[j]
        ds2 = np.concatenate(datasets[:i+1])
        match = mnn(ds1, ds2, knn=knn, approx=approx)
        
        ds_ind = [ a for a, _ in match ]
        ref_ind = [ b for _, b in match ]
                    
        bias = transform(ds1, ds2, ds_ind, ref_ind, sigma,
                         batch_size=batch_size)
        datasets[j] = ds1 + bias

    return datasets

def interpret_alignments(datasets, expr_datasets, genes,
                         verbose=VERBOSE, knn=KNN, approx=APPROX,
                         alpha=ALPHA, n_permutations=None):
    if n_permutations is None:
        n_permutations = float(len(genes) * 30)
    
    alignments, matches = find_alignments(
        datasets, knn=knn, approx=approx, alpha=alpha, verbose=verbose
    )

    for i, j in alignments:
        # Compute average bias vector that aligns two datasets together.
        ds_i = expr_datasets[i]
        ds_j = expr_datasets[j]
        if i < j:
            match = matches[(i, j)]
        else:
            match = matches[(j, i)]
        i_ind = [ a for a, _ in match ]
        j_ind = [ b for _, b in match ]
        avg_bias = np.absolute(
            np.mean(ds_j[j_ind, :] - ds_i[i_ind, :], axis=0)
        )

        # Construct null distribution and compute p-value.
        null_bias = (
            ds_j[np.random.randint(ds_j.shape[0], size=n_permutations), :] -
            ds_i[np.random.randint(ds_i.shape[0], size=n_permutations), :]
        )
        p = ((np.sum(np.greater_equal(
            np.absolute(np.tile(avg_bias, (n_permutations, 1))),
            np.absolute(null_bias)
        ), axis=0, dtype=float) + 1) / (n_permutations + 1))

        print('>>>> Stats for alignment {}'.format((i, j)))
        for k in range(len(p)):
            print('{}\t{}'.format(genes[k], p[k]))